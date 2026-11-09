"""Checks that compare requirements in pairs (BUILD_PLAN Step 7, part B): possible duplicates, possible
conflicts and change requests.

1. Every open requirement (not rejected or merged) gets an embedding of its current statement
   (app/nlp/similarity.py); the cosine similarity of each pair says how close they are.
2. Pairs at CONFLICT_CANDIDATE or more go through the NLI model both ways (app/nlp/nli.py).
3. classify_pair turns the two scores into a candidate: at most one finding per pair. It goes on the newer
   requirement (higher REQ number) and points at the older one.
4. The LLM checks every candidate (batched; answers kept per pair of versions in requirement_pair_verdicts).
   settle_pair keeps the finding only when the LLM sees a duplicate, conflict or change; the LLM's reason is
   stored with it. Similar words alone fooled the models: "reset password by email" is not a change of
   "verify email after login". Without an answering LLM the candidate is kept, marked "unconfirmed".

Whether a conflict counts as a change request depends on the older requirement being accepted, which is plain
code (CLAUDE.md rule 4), as is the wording, which stays cautious (rule 7). Like the rule-based checks, only
findings still origin "ai" and status "suggested" are updated or removed (rule 2).
"""

import logging
import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.ai.llm import LLMClient, LLMError, now, thinking_level
from app.core.config import settings
from app.models import PairVerdict, Project, Requirement, RequirementVersion, ValidationFinding
from app.models.validation import PAIR_CATEGORIES
from app.nlp import nli, similarity
from app.nlp.similarity import ModelUnavailable
from app.schemas.pairs import PairJudgements

logger = logging.getLogger(__name__)

# Cosine similarity from which two requirements may say the same thing
DUPLICATE = 0.85
# Similarity from which a pair is checked for a contradiction
CONFLICT_CANDIDATE = 0.5
# Contradiction probability (higher of both directions) from which a pair may conflict
CONTRADICTION = 0.7
# The change request gate (similarity to an accepted requirement) is settings.change_request_similarity

# Pairs per LLM call, and the time one check may wait for the LLM (it can run inside an accept or an edit)
PAIR_BATCH_SIZE = 15
LLM_SECONDS = 45

# The LLM's relation → finding category; None: no finding. A conflict with an accepted requirement is a change
# request, and a change of one not accepted yet is a conflict between two suggestions.
RELATION_CATEGORY: dict[str, str | None] = {
    "same": "duplicate",
    "duplicate": "duplicate",
    "conflict": "conflict",
    "change_request": "conflict",
    "related_but_different": None,
    "unrelated": None,
}

# Statuses that take no part: nothing is compared with them
CLOSED = ("rejected", "merged")


@dataclass(frozen=True)
class PairItem:
    """What a pair check needs to know about one requirement."""

    id: int
    code: str
    number: int
    status: str
    statement: str


@dataclass(frozen=True)
class PairDraft:
    owner_id: int  # the newer requirement, which gets the finding
    related_id: int  # the older one
    category: str
    rule: str
    severity: str
    reason: str
    evidence: str  # the older requirement's statement, shown next to the newer one
    score: float
    confirmation: str | None = None  # "confirmed" by the LLM, or "unconfirmed"
    explanation: str | None = None  # the LLM's reason

    @property
    def key(self) -> tuple[int, str, int]:
        """Identifies a finding across re-runs."""
        return (self.owner_id, self.rule, self.related_id)


_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")  # \d also matches Bengali digits


def _numbers(text: str) -> set[str]:
    normal = unicodedata.normalize("NFC", text)
    found = set()
    for match in _NUMBER.findall(normal):
        digits = "".join(str(unicodedata.digit(c)) if c.isdigit() else c for c in match)
        found.add(digits.replace(",", ""))
    return found


def numbers_differ(a: str, b: str) -> bool:
    """The two statements give different numbers ("2 seconds" and "5 seconds"), or only one gives any."""
    first, second = _numbers(a), _numbers(b)
    return bool(first or second) and first != second


def describe(category: str, old: PairItem, new: PairItem, s: float, c: float | None) -> PairDraft:
    """The finding of this category for the pair, with a cautious reason built from the scores."""
    contradicts = c is not None and c >= CONTRADICTION
    if category == "change_request":
        if contradicts:
            how = f"may contradict it (contradiction {c:.2f})"
        elif numbers_differ(old.statement, new.statement):
            how = "gives different numbers"
        else:
            how = "says something different"
        reason = (
            f"Possible change request for {old.code}, which is already accepted: this one is similar "
            f"(similarity {s:.2f}) but {how}. The client may have changed their mind, so it may need a decision."
        )
        return PairDraft(new.id, old.id, category, "change_request", "medium", reason, old.statement, round(s, 3))
    if category == "conflict":
        if contradicts:
            reason = (
                f"Possible conflict with {old.code}: the model gives a {c:.2f} chance that the two statements "
                "contradict each other. One of them may need to change."
            )
        else:
            reason = f"Possible conflict with {old.code}: the two statements may not both hold. One may need to change."
        score = c if c is not None else s
        return PairDraft(new.id, old.id, category, "possible_conflict", "high", reason, old.statement, round(score, 3))
    reason = (
        f"Possible duplicate of {old.code} (similarity {s:.2f}). If both mean the same, merging keeps every "
        "source sentence in one requirement."
    )
    return PairDraft(new.id, old.id, "duplicate", "possible_duplicate", "medium", reason, old.statement, round(s, 3))


def classify_pair(old: PairItem, new: PairItem, similarity_score: float, contradiction: float | None) -> PairDraft | None:
    """The candidate finding from the scores alone; `old` has the lower REQ number. `contradiction` is None when
    NLI did not run (similarity below CONFLICT_CANDIDATE)."""
    if old.id == new.id:
        return None
    s = float(similarity_score)
    c = float(contradiction) if contradiction is not None and s >= CONFLICT_CANDIDATE else None
    contradicts = c is not None and c >= CONTRADICTION
    differs = s < DUPLICATE or contradicts or numbers_differ(old.statement, new.statement)
    if old.status == "accepted" and s >= settings.change_request_similarity and differs:
        return describe("change_request", old, new, s, c)
    if contradicts:
        return describe("conflict", old, new, s, c)
    if s >= DUPLICATE:
        return describe("duplicate", old, new, s, c)
    return None


def settle_pair(
    candidate: PairDraft,
    relation: str | None,
    reason: str | None,
    old: PairItem,
    new: PairItem,
    s: float,
    c: float | None,
) -> PairDraft | None:
    """The finding after the LLM's check. `relation` is None when no LLM answered: the candidate stays, unconfirmed.

    "Not enough information" also leaves it unconfirmed. Otherwise the LLM's relation decides the category, or
    that there is no finding at all.
    """
    if relation is None or relation == "not_enough_information":
        return replace(candidate, confirmation="unconfirmed", explanation=reason)
    category = RELATION_CATEGORY[relation]
    if category is None:
        return None
    if category == "conflict" and old.status == "accepted":
        category = "change_request"
    return replace(describe(category, old, new, s, c), confirmation="confirmed", explanation=reason)


def _item(r: Requirement) -> PairItem:
    return PairItem(id=r.id, code=r.code, number=r.number, status=r.status, statement=r.current.statement)


def _is_open(finding: ValidationFinding) -> bool:
    return finding.origin == "ai" and finding.status == "suggested"


def judge_pairs(
    db: Session, llm: LLMClient | None, pairs: Sequence[tuple[RequirementVersion, RequirementVersion]]
) -> dict[tuple[int, int], tuple[str, str]]:
    """The LLM's (relation, reason) for each (older, newer) version pair, by version ids. The caller commits.

    Stored answers are used first; the rest go to the LLM in batches and are stored. "Not enough information"
    is not stored, so it is asked again next time. A pair with no answer (no LLM, an LLM error, an answer that
    skipped it) is missing from the result.
    """
    if not pairs:
        return {}
    older_ids = {old.id for old, _ in pairs}
    newer_ids = {new.id for _, new in pairs}
    answers: dict[tuple[int, int], tuple[str, str]] = {}
    for verdict in db.scalars(
        select(PairVerdict).where(
            PairVerdict.older_version_id.in_(older_ids), PairVerdict.newer_version_id.in_(newer_ids)
        )
    ):
        answers[(verdict.older_version_id, verdict.newer_version_id)] = (verdict.relation, verdict.reason)
    missing = list(dict.fromkeys((old, new) for old, new in pairs if (old.id, new.id) not in answers))
    if llm is None or not missing:
        return answers
    new_verdicts: list[PairVerdict] = []

    deadline = now() + LLM_SECONDS
    for start in range(0, len(missing), PAIR_BATCH_SIZE):
        batch = missing[start : start + PAIR_BATCH_SIZE]
        payload = [
            {"pair_id": f"p{n}", "older": old.statement, "newer": new.statement} for n, (old, new) in enumerate(batch, 1)
        ]
        try:
            result = llm.complete_json(
                "classify_pairs",
                {"pairs": payload},
                PairJudgements,
                thinking=thinking_level(settings.llm_extraction_thinking),
                deadline=deadline,
            )
        except LLMError as exc:  # the remaining pairs stay unconfirmed
            logger.warning("pair check: the LLM did not answer (%s); %d pair(s) unconfirmed", exc, len(missing) - start)
            break
        by_id = {j.pair_id: j for j in result.pairs}
        for n, (old, new) in enumerate(batch, 1):
            judgement = by_id.get(f"p{n}")
            if judgement is None:
                continue
            answers[(old.id, new.id)] = (judgement.relation, judgement.reason)
            if judgement.relation != "not_enough_information":
                new_verdicts.append(
                    PairVerdict(
                        older_version_id=old.id,
                        newer_version_id=new.id,
                        relation=judgement.relation,
                        reason=judgement.reason,
                        ai_model=llm.last_model,
                    )
                )
    # Verdicts are a cache: one for a version deleted meanwhile is not stored (it would break the foreign key)
    existing = similarity.existing_version_ids(
        db, [v.older_version_id for v in new_verdicts] + [v.newer_version_id for v in new_verdicts]
    )
    db.add_all(v for v in new_verdicts if v.older_version_id in existing and v.newer_version_id in existing)
    db.flush()
    return answers


def check_similarity(
    db: Session, project: Project, changed_ids: Iterable[int] | None = None, llm: LLMClient | None = None
) -> str | None:
    """Store the pair findings of the project. The caller commits.

    `changed_ids`: only pairs with at least one of these requirements are checked (new, edited or reviewed
    ones); None checks every pair. `llm` checks the candidate pairs; without it they are kept unconfirmed.
    Returns None, or why the similarity models could not run.

    Suggested findings that involve a rejected or merged requirement are removed in any case, even without
    the models.
    """
    # Fresh from the database (pending changes written first): never compare cached objects of requirements that
    # were replaced or deleted since they were loaded
    db.flush()
    requirements = db.scalars(
        select(Requirement)
        .where(Requirement.project_id == project.id)
        .options(selectinload(Requirement.versions), selectinload(Requirement.findings))
        .execution_options(populate_existing=True)
    ).all()
    candidates = sorted((r for r in requirements if r.status not in CLOSED), key=lambda r: r.number)
    open_ids = {r.id for r in candidates}
    changed = None if changed_ids is None else set(changed_ids)

    for requirement in requirements:
        for finding in list(requirement.findings):
            if (
                finding.category in PAIR_CATEGORIES
                and _is_open(finding)
                and (requirement.id not in open_ids or finding.related_requirement_id not in open_ids)
            ):
                requirement.findings.remove(finding)

    if len(candidates) < 2 or (changed is not None and not changed & open_ids):
        return None  # nothing to compare: no need to load the models
    try:
        embedder = similarity.get_embedder()
        nli_model = nli.get_nli()
    except ModelUnavailable as exc:
        return str(exc)

    try:  # a model failing while it runs must not break the edit or review that asked for the check
        vectors = similarity.ensure_embeddings(db, embedder, [r.current for r in candidates])
        # A version deleted meanwhile (another extraction replaced its suggestion) is left out of the batch
        candidates = [r for r in candidates if r.current.id in vectors]
        scores = similarity.similarity_matrix([vectors[r.current.id] for r in candidates])
        pairs = []
        for i, j in np.argwhere(np.triu(scores >= CONFLICT_CANDIDATE, k=1)):
            old, new = candidates[int(i)], candidates[int(j)]
            if changed is None or old.id in changed or new.id in changed:
                pairs.append((old, new, float(scores[i, j])))
        contradictions = nli.contradiction_both_ways(
            nli_model, [(old.current.statement, new.current.statement) for old, new, _ in pairs]
        )
    except Exception as exc:
        logger.exception("Similarity checks failed")
        return f"The similarity checks failed: {exc}"[:500]

    flagged = []
    for (old, new, s), c in zip(pairs, contradictions, strict=True):
        candidate = classify_pair(_item(old), _item(new), s, c)
        if candidate is not None:
            flagged.append((old, new, s, c, candidate))
    verdicts = judge_pairs(db, llm, [(old.current, new.current) for old, new, *_ in flagged])
    wanted: dict[tuple[int, str, int], PairDraft] = {}
    for old, new, s, c, candidate in flagged:
        relation, reason = verdicts.get((old.current.id, new.current.id), (None, None))
        found = settle_pair(candidate, relation, reason, _item(old), _item(new), s, c)
        if found is not None:
            wanted[found.key] = found

    by_id = {r.id: r for r in candidates}
    for requirement in candidates:
        for finding in list(requirement.findings):
            if finding.category not in PAIR_CATEGORIES or not _is_open(finding):
                continue
            related = finding.related_requirement_id
            if changed is not None and requirement.id not in changed and related not in changed:
                continue  # this pair was not checked
            found = wanted.get((requirement.id, finding.rule, related))
            if found is None:
                requirement.findings.remove(finding)
            else:
                finding.version = requirement.current.version
                finding.category, finding.severity, finding.reason = found.category, found.severity, found.reason
                finding.evidence, finding.score = found.evidence, found.score
                finding.confirmation, finding.explanation = found.confirmation, found.explanation

    stored = {
        (r.id, f.rule, f.related_requirement_id) for r in candidates for f in r.findings if f.category in PAIR_CATEGORIES
    }
    for key, found in wanted.items():
        if key in stored:  # also when a person already decided on it
            continue
        owner = by_id[found.owner_id]
        owner.findings.append(
            ValidationFinding(
                version=owner.current.version,
                category=found.category,
                rule=found.rule,
                severity=found.severity,
                reason=found.reason,
                evidence=found.evidence,
                related_requirement_id=found.related_id,
                score=found.score,
                confirmation=found.confirmation,
                explanation=found.explanation,
                origin="ai",
                status="suggested",
            )
        )
    return None
