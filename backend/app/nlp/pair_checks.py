"""Checks that compare requirements in pairs (BUILD_PLAN Step 7, part B): possible duplicates, possible
conflicts and change requests.

1. Every open requirement (not rejected or merged) gets an embedding of its current statement
   (app/nlp/similarity.py); the cosine similarity of each pair says how close they are.
2. Pairs at CONFLICT_CANDIDATE or more go through the NLI model both ways (app/nlp/nli.py).
3. classify_pair turns the two scores into at most one finding. It goes on the newer requirement (higher REQ
   number) and points at the older one, so a pair never gets two findings.

The models only give scores; which finding a pair gets is plain code (CLAUDE.md rule 4), and the wording stays
cautious (rule 7). Like the rule-based checks, only findings still origin "ai" and status "suggested" are
updated or removed (rule 2).
"""

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Project, Requirement, ValidationFinding
from app.models.validation import PAIR_CATEGORIES
from app.nlp import nli, similarity
from app.nlp.similarity import ModelUnavailable

# Cosine similarity from which two requirements may say the same thing
DUPLICATE = 0.85
# Similarity from which a pair is checked for a contradiction
CONFLICT_CANDIDATE = 0.5
# Contradiction probability (higher of both directions) from which a pair may conflict
CONTRADICTION = 0.7
# Similarity to an accepted requirement from which a different statement may be a change request
CHANGE_CANDIDATE = 0.6

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


def classify_pair(old: PairItem, new: PairItem, similarity_score: float, contradiction: float | None) -> PairDraft | None:
    """At most one finding for the pair; `old` has the lower REQ number. `contradiction` is None when NLI did not
    run (similarity below CONFLICT_CANDIDATE)."""
    if old.id == new.id:
        return None
    s = float(similarity_score)
    c = float(contradiction) if contradiction is not None and s >= CONFLICT_CANDIDATE else None
    contradicts = c is not None and c >= CONTRADICTION
    other_numbers = numbers_differ(old.statement, new.statement)

    def draft(category: str, rule: str, severity: str, reason: str, score: float) -> PairDraft:
        return PairDraft(new.id, old.id, category, rule, severity, reason, old.statement, round(score, 3))

    if old.status == "accepted" and s >= CHANGE_CANDIDATE and (s < DUPLICATE or contradicts or other_numbers):
        if contradicts:
            how = f"may contradict it (contradiction {c:.2f})"
        elif other_numbers:
            how = "gives different numbers"
        else:
            how = "says something different"
        reason = (
            f"Possible change request for {old.code}, which is already accepted: this one is similar "
            f"(similarity {s:.2f}) but {how}. The client may have changed their mind, so it may need a decision."
        )
        return draft("change_request", "change_request", "medium", reason, s)
    if contradicts:
        reason = (
            f"Possible conflict with {old.code}: the model gives a {c:.2f} chance that the two statements "
            "contradict each other. One of them may need to change."
        )
        return draft("conflict", "possible_conflict", "high", reason, c)
    if s >= DUPLICATE:
        reason = (
            f"Possible duplicate of {old.code} (similarity {s:.2f}). If both mean the same, merging keeps every "
            "source sentence in one requirement."
        )
        return draft("duplicate", "possible_duplicate", "medium", reason, s)
    return None


def _item(r: Requirement) -> PairItem:
    return PairItem(id=r.id, code=r.code, number=r.number, status=r.status, statement=r.current.statement)


def _is_open(finding: ValidationFinding) -> bool:
    return finding.origin == "ai" and finding.status == "suggested"


def check_similarity(db: Session, project: Project, changed_ids: Iterable[int] | None = None) -> str | None:
    """Store the pair findings of the project. The caller commits.

    `changed_ids`: only pairs with at least one of these requirements are checked (new, edited or reviewed
    ones); None checks every pair. Returns None, or why the similarity checks could not run.

    Suggested findings that involve a rejected or merged requirement are removed in any case, even without
    the models.
    """
    requirements = db.scalars(
        select(Requirement)
        .where(Requirement.project_id == project.id)
        .options(selectinload(Requirement.versions), selectinload(Requirement.findings))
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

    vectors = similarity.ensure_embeddings(db, embedder, [r.current for r in candidates])
    scores = similarity.similarity_matrix([vectors[r.current.id] for r in candidates])
    pairs = []
    for i, j in np.argwhere(np.triu(scores >= CONFLICT_CANDIDATE, k=1)):
        old, new = candidates[int(i)], candidates[int(j)]
        if changed is None or old.id in changed or new.id in changed:
            pairs.append((old, new, float(scores[i, j])))
    contradictions = nli.contradiction_both_ways(
        nli_model, [(old.current.statement, new.current.statement) for old, new, _ in pairs]
    )
    wanted: dict[tuple[int, str, int], PairDraft] = {}
    for (old, new, s), c in zip(pairs, contradictions, strict=True):
        found = classify_pair(_item(old), _item(new), s, c)
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
                origin="ai",
                status="suggested",
            )
        )
    return None
