"""The traceability matrix (Step 10): one row per approved requirement with its links and coverage.

Used by the Traceability page, the requirement panel and the CSV export. Dismissed links (the AI said "no link")
are not shown; rejected links are, so a person can undo the rejection.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.models import Repository, Requirement, TraceLink
from app.schemas.trace import (
    CriterionCoverage,
    RepoOut,
    TraceLinkOut,
    TraceMatrix,
    TraceRequirement,
    TraceRow,
    TraceSummary,
)
from app.trace.coverage import LEVELS, CaseRef, CriterionRef, coverage
from app.trace.targets import all_targets, github_url

REVIEWED = ("accepted", "edited")
_STATUS_ORDER = {"accepted": 0, "suggested": 1, "rejected": 2}


def repo_out(repo: Repository) -> RepoOut:
    return RepoOut(
        full_name=repo.full_name,
        owner=repo.owner,
        name=repo.name,
        url=repo.html_url,
        default_branch=repo.default_branch,
        private=repo.private,
        head_sha=repo.head_sha,
        last_analyzed_at=repo.last_analyzed_at,
        last_result=repo.last_result,
        token_configured=bool((settings.github_token or "").strip()),
    )


def criteria_refs(r: Requirement) -> list[CriterionRef]:
    return [CriterionRef(c.id, c.code, c.current.text) for c in r.criteria if c.status in REVIEWED]


def case_refs(r: Requirement) -> list[CaseRef]:
    return [
        CaseRef(t.id, t.number, t.code, t.current.title, t.current.criterion_id) for t in r.live_test_cases
    ]


def link_out(link: TraceLink, repo: Repository, present: set[tuple[str, str]], matches: list[str]) -> TraceLinkOut:
    return TraceLinkOut(
        id=link.id,
        link_type=link.link_type,
        target_type=link.target_type,
        target_key=link.target_key,
        target_label=link.target_label,
        target_detail=link.target_detail,
        url=github_url(repo, link.target_type, link.target_key, link.target_line),
        origin=link.origin,
        status=link.status,
        confidence=link.confidence,
        similarity=link.similarity,
        reason=link.reason,
        evidence=link.evidence,
        ai_model=link.ai_model,
        llm_checked=link.llm_checked,
        live=link.live,
        present=(link.target_type, link.target_key) in present,
        test_cases=matches,
        decided_by=link.decided_by.name if link.decided_by else None,
        decided_at=link.decided_at,
    )


def _sort_key(link: TraceLink) -> tuple:
    return (_STATUS_ORDER.get(link.status, 3), link.origin != "explicit", -(link.confidence or 0), link.target_label)


def trace_row(r: Requirement, links: list[TraceLink], repo: Repository | None, present: set[tuple[str, str]]) -> TraceRow:
    shown = sorted((link for link in links if link.status != "dismissed"), key=_sort_key)
    result = coverage(shown, criteria_refs(r), case_refs(r))
    outs = [link_out(link, repo, present, result.matches.get(link.id, [])) for link in shown] if repo else []
    return TraceRow(
        requirement=TraceRequirement(id=r.id, code=r.code, statement=r.current.statement, module_name=r.module_name),
        coverage=result.level,
        coverage_reason=result.reason,
        criteria=[
            CriterionCoverage(code=c.criterion.code, text=c.criterion.text, covered=c.covered, test_cases=c.test_cases)
            for c in result.criteria
        ],
        pending=sum(1 for link in shown if link.pending),
        implements=[o for o in outs if o.link_type == "implements"],
        verifies=[o for o in outs if o.link_type == "verifies"],
    )


def links_by_requirement(db: Session, repo: Repository | None) -> dict[int, list[TraceLink]]:
    if repo is None:
        return {}
    links = db.scalars(
        select(TraceLink).where(TraceLink.repository_id == repo.id).options(selectinload(TraceLink.decided_by))
    ).all()
    grouped: dict[int, list[TraceLink]] = {}
    for link in links:
        grouped.setdefault(link.requirement_id, []).append(link)
    return grouped


def present_keys(db: Session, repo: Repository | None) -> set[tuple[str, str]]:
    return {(t.target_type, t.key) for t in all_targets(db, repo)} if repo else set()


def build_matrix(
    db: Session,
    repo: Repository | None,
    requirements: list[Requirement],
    *,
    module: str | None = None,
    coverage_level: str | None = None,
    link_type: str | None = None,
    pending: bool | None = None,
) -> TraceMatrix:
    """Rows for the approved `requirements`; the summary counts all of them, the filters only limit the rows."""
    grouped = links_by_requirement(db, repo)
    present = present_keys(db, repo)
    rows = [trace_row(r, grouped.get(r.id, []), repo, present) for r in requirements]
    counts = {level: 0 for level in LEVELS}
    for row in rows:
        counts[row.coverage] += 1
    summary = TraceSummary(total=len(rows), counts=counts, pending=sum(row.pending for row in rows))
    modules = sorted({r.module_name for r in requirements if r.module_name})

    def keep(row: TraceRow) -> bool:
        if module is not None and (row.requirement.module_name or "") != module:
            return False
        if coverage_level is not None and row.coverage != coverage_level:
            return False
        if link_type is not None and not (row.implements if link_type == "implements" else row.verifies):
            return False
        if pending is not None and (row.pending > 0) != pending:
            return False
        return True

    return TraceMatrix(
        repo=repo_out(repo) if repo else None,
        summary=summary,
        modules=modules,
        rows=[row for row in rows if keep(row)],
    )
