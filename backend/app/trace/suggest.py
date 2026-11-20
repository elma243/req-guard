"""Suggested trace links: embedding similarity, then a check by the LLM (Step 10).

1. The requirement statement (current version) and each target's text (app/trace/targets.py) are embedded with the
   similarity model (all-MiniLM-L6-v2). Requirement embeddings are cached per version, target embeddings by a hash
   of the text, so an unchanged repository is not embedded again.
2. Per requirement and target type, the 3 most similar targets with similarity >= TRACE_SIMILARITY are candidates.
3. The LLM checks the candidates ("verify_links", through app/ai/llm.py only): "link" stays as an AI suggestion
   with its reason and confidence; "no_link" and "not enough information" are kept as `dismissed` (hidden).
   Without an answering LLM a candidate is still suggested, marked "not checked by AI", and checked next time.

Never suggested again: any target that already has a link row for the requirement (rejected by a person,
accepted, added by a person, explicit). Asked again: a dismissed link from an older requirement version, and a
suggestion the LLM could not check yet.
"""

import hashlib
import logging
from dataclasses import dataclass, field

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.llm import LLMClient, LLMError, now
from app.core.config import settings
from app.models import Repository, Requirement, TraceLink, TraceTargetEmbedding
from app.nlp import similarity
from app.nlp.similarity import ModelUnavailable
from app.schemas.trace import LinkVerdicts
from app.trace.targets import Target

logger = logging.getLogger(__name__)

TOP_K = 3
BATCH_SIZE = 15
LLM_SECONDS = 90.0
# Target types compared with requirements (test files are reached through their test functions)
SUGGESTED_TYPES = ("file", "test_function", "commit", "pr")


@dataclass
class Candidate:
    requirement: Requirement
    target: Target
    similarity: float
    existing: TraceLink | None = None


@dataclass
class SuggestResult:
    added: int = 0
    dismissed: int = 0
    unchecked: int = 0
    notices: list[str] = field(default_factory=list)


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def target_vectors(db: Session, repo: Repository, embedder, texts: list[str]) -> list[np.ndarray]:
    """Embedding of each text, from the cache when the same text was embedded before. The caller commits."""
    hashes = [text_hash(t) for t in texts]
    stored: dict[str, list[float]] = {}
    unique = list(dict.fromkeys(hashes))
    for chunk in (unique[i : i + 500] for i in range(0, len(unique), 500)):
        rows = db.execute(
            select(TraceTargetEmbedding.text_hash, TraceTargetEmbedding.embedding).where(
                TraceTargetEmbedding.repository_id == repo.id,
                TraceTargetEmbedding.model == embedder.name,
                TraceTargetEmbedding.text_hash.in_(chunk),
            )
        ).all()
        stored.update({h: v for h, v in rows})
    missing: dict[str, str] = {}
    for h, t in zip(hashes, texts, strict=True):
        if h not in stored and h not in missing:
            missing[h] = t
    if missing:
        vectors = embedder.encode(list(missing.values()))
        for h, vector in zip(missing, vectors, strict=True):
            db.add(TraceTargetEmbedding(repository_id=repo.id, text_hash=h, model=embedder.name, embedding=vector))
            stored[h] = vector
    return [np.asarray(stored[h], dtype=np.float32) for h in hashes]


def can_ask_again(link: TraceLink, requirement: Requirement) -> bool:
    """Only an AI row nobody decided on: dismissed for an older version, or never checked by an LLM."""
    if link.origin != "ai":
        return False
    if link.status == "dismissed":
        return link.requirement_version != requirement.current.version
    return link.status == "suggested" and not link.llm_checked


def find_candidates(
    db: Session,
    repo: Repository,
    requirements: list[Requirement],
    targets: list[Target],
    existing: dict[tuple[int, str, str], TraceLink],
    result: SuggestResult,
) -> list[Candidate]:
    try:
        embedder = similarity.get_embedder()
    except ModelUnavailable as exc:
        result.notices.append(f"Only explicit links were found: {exc}")
        return []
    targets = [t for t in targets if t.target_type in SUGGESTED_TYPES and t.text.strip()]
    if not targets or not requirements:
        return []
    versions = similarity.ensure_embeddings(db, embedder, [r.current for r in requirements])
    target_matrix = np.vstack(target_vectors(db, repo, embedder, [t.text for t in targets]))
    threshold = settings.trace_similarity

    candidates: list[Candidate] = []
    for requirement in requirements:
        vector = versions.get(requirement.current.id)
        if vector is None:
            continue
        scores = np.clip(target_matrix @ vector, -1.0, 1.0)
        for target_type in SUGGESTED_TYPES:
            ranked = sorted(
                (i for i, t in enumerate(targets) if t.target_type == target_type and scores[i] >= threshold),
                key=lambda i: -scores[i],
            )
            picked = 0
            for i in ranked:
                if picked == TOP_K:
                    break
                target = targets[i]
                link = existing.get((requirement.id, target.target_type, target.key))
                if link is not None and not can_ask_again(link, requirement):
                    continue
                candidates.append(Candidate(requirement, target, round(float(scores[i]), 3), link))
                picked += 1
    return candidates


def _pair(pair_id: str, c: Candidate) -> dict:
    criteria = [
        a.current.text for a in c.requirement.criteria if a.status in ("accepted", "edited")
    ]
    kinds = {"file": "code file", "test_function": "test function", "commit": "commit", "pr": "pull request"}
    return {
        "pair_id": pair_id,
        "requirement": {"code": c.requirement.code, "statement": c.requirement.current.statement, "criteria": criteria},
        "item": {"type": kinds[c.target.target_type], "name": c.target.label, "text": c.target.text},
    }


def _store(
    db: Session,
    project_id: int,
    repo: Repository,
    c: Candidate,
    existing: dict[tuple[int, str, str], TraceLink],
    *,
    status: str,
    reason: str,
    confidence: float | None,
    ai_model: str | None,
    llm_checked: bool,
) -> None:
    link = c.existing
    if link is None:
        link = TraceLink(
            project_id=project_id,
            repository_id=repo.id,
            requirement_id=c.requirement.id,
            link_type=c.target.link_type,
            target_type=c.target.target_type,
            target_key=c.target.key,
            origin="ai",
        )
        db.add(link)
        existing[(c.requirement.id, c.target.target_type, c.target.key)] = link
    link.target_label, link.target_detail, link.target_line = c.target.label, c.target.detail, c.target.line
    link.status, link.reason, link.confidence = status, reason[:1000], confidence
    link.similarity, link.ai_model, link.llm_checked = c.similarity, ai_model, llm_checked
    link.requirement_version = c.requirement.current.version


def suggest_links(
    db: Session,
    project_id: int,
    repo: Repository,
    requirements: list[Requirement],
    targets: list[Target],
    existing: dict[tuple[int, str, str], TraceLink],
    llm: LLMClient | None,
) -> SuggestResult:
    """Find candidates and let the LLM check them. The caller commits."""
    result = SuggestResult()
    candidates = find_candidates(db, repo, requirements, targets, existing, result)
    if not candidates:
        return result

    def unchecked(c: Candidate, why: str) -> None:
        _store(
            db, project_id, repo, c, existing,
            status="suggested",
            reason=f"Similarity {c.similarity:.2f}; not checked by AI ({why}).",
            confidence=None,
            ai_model=None,
            llm_checked=False,
        )
        result.added += c.existing is None
        result.unchecked += 1

    if llm is None:
        for c in candidates:
            unchecked(c, "no AI model is configured")
        result.notices.append("Suggestions were not checked by AI: no AI model is configured.")
        return result

    deadline = now() + LLM_SECONDS
    failure: str | None = None
    for start in range(0, len(candidates), BATCH_SIZE):
        batch = candidates[start : start + BATCH_SIZE]
        if failure is None:
            pairs = [_pair(f"P{i + 1}", c) for i, c in enumerate(batch)]
            try:
                answer = llm.complete_json("verify_links", {"pairs": pairs}, LinkVerdicts, deadline=deadline)
            except LLMError as exc:
                failure = str(exc)
                logger.warning("verify_links failed: %s", exc)
            else:
                verdicts = {v.pair_id: v for v in answer.links}
                for i, c in enumerate(batch):
                    verdict = verdicts.get(f"P{i + 1}")
                    if verdict is None:
                        unchecked(c, "the AI gave no answer for it")
                        continue
                    linked = verdict.verdict == "link"
                    _store(
                        db, project_id, repo, c, existing,
                        status="suggested" if linked else "dismissed",
                        reason=verdict.reason or ("Possible link." if linked else "Not enough information."),
                        confidence=round(verdict.confidence, 2),
                        ai_model=llm.last_model,
                        llm_checked=True,
                    )
                    if linked:
                        result.added += c.existing is None or c.existing.status == "dismissed"
                    else:
                        result.dismissed += 1
                continue
        for c in batch:
            unchecked(c, failure)
    if failure is not None:
        result.notices.append(f"Some suggestions were not checked by AI: {failure}")
    return result
