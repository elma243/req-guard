"""Trace links and the traceability matrix (Step 10). Owner only (the owner is the team lead).

Explicit links (the requirement ID found next to the code) count at once; AI suggestions count only once a person
confirms them; a person can reject any link or add one by hand (rule 2). Rejected links are never suggested again.
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import CurrentUser, DbSession, OptionalLLM, OwnedProject
from app.api.repos import project_repo, require_repo
from app.code.analyze import approved_requirements, recover_links
from app.models import Project, Repository, Requirement, TestCase, TraceLink
from app.models.trace import TARGET_LINK_TYPE
from app.reports.test_case_export import export_filename
from app.reports.trace_export import build_csv
from app.schemas.trace import (
    Coverage,
    LinkCreate,
    LinkType,
    RecoverResult,
    TargetOut,
    TargetType,
    TraceLinkOut,
    TraceMatrix,
    TraceRow,
)
from app.trace.coverage import match_test_cases
from app.trace.matrix import build_matrix, case_refs, link_out, present_keys, trace_row
from app.trace.targets import all_targets, github_url

router = APIRouter(prefix="/projects/{project_id}/trace", tags=["trace"])

MAX_TARGETS = 50


def _link(db: Session, project: Project, link_id: int) -> TraceLink:
    link = db.scalar(
        select(TraceLink)
        .where(TraceLink.id == link_id, TraceLink.project_id == project.id)
        .options(selectinload(TraceLink.decided_by))
    )
    if link is None or link.status == "dismissed":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Link not found")
    return link


def _approved(db: Session, project: Project, requirement_id: int) -> Requirement:
    requirement = next((r for r in approved_requirements(db, project.id) if r.id == requirement_id), None)
    if requirement is None:
        exists = db.scalar(
            select(Requirement.id).where(Requirement.id == requirement_id, Requirement.project_id == project.id)
        )
        if exists is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Requirement not found")
        raise HTTPException(status.HTTP_409_CONFLICT, "Only approved requirements get trace links.")
    return requirement


def _link_result(db: Session, repo: Repository, link: TraceLink) -> TraceLinkOut:
    requirement = _approved_or_any(db, link.requirement_id)
    cases = case_refs(requirement) if requirement else []
    matches = [c.code for c in match_test_cases(link, cases)]
    return link_out(link, repo, present_keys(db, repo), matches)


def _approved_or_any(db: Session, requirement_id: int) -> Requirement | None:
    return db.scalar(
        select(Requirement)
        .where(Requirement.id == requirement_id)
        .options(selectinload(Requirement.test_cases).selectinload(TestCase.versions))
    )


@router.get("")
def get_matrix(
    project: OwnedProject,
    db: DbSession,
    module: Annotated[str | None, Query(max_length=100)] = None,
    coverage: Coverage | None = None,
    link_type: LinkType | None = None,
    pending: bool | None = None,
) -> TraceMatrix:
    return build_matrix(
        db,
        project_repo(db, project),
        approved_requirements(db, project.id),
        module=module,
        coverage_level=coverage,
        link_type=link_type,
        pending=pending,
    )


@router.post("/recover")
def recover(project: OwnedProject, db: DbSession, llm: OptionalLLM) -> RecoverResult:
    """Find links again from the stored analysis, without asking GitHub (e.g. after approving more requirements)."""
    repo = require_repo(db, project)
    if repo.head_sha is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Analyse the repository first.")
    result = recover_links(db, project.id, repo, llm)
    db.commit()
    return result


@router.get("/requirements/{requirement_id}")
def requirement_trace(requirement_id: int, project: OwnedProject, db: DbSession) -> TraceRow:
    requirement = _approved(db, project, requirement_id)
    repo = project_repo(db, project)
    links = []
    if repo is not None:
        links = db.scalars(
            select(TraceLink)
            .where(TraceLink.repository_id == repo.id, TraceLink.requirement_id == requirement.id)
            .options(selectinload(TraceLink.decided_by))
        ).all()
    return trace_row(requirement, list(links), repo, present_keys(db, repo))


def _decide(db: Session, project: Project, link_id: int, user, new_status: str) -> TraceLinkOut:
    link = _link(db, project, link_id)
    repo = require_repo(db, project)
    link.status = new_status
    link.decided_by_id = user.id
    link.decided_at = datetime.now(UTC)
    db.commit()
    db.refresh(link)
    return _link_result(db, repo, link)


@router.post("/links/{link_id}/confirm")
def confirm_link(link_id: int, project: OwnedProject, db: DbSession, user: CurrentUser) -> TraceLinkOut:
    """Confirm a suggestion, or undo a rejection."""
    return _decide(db, project, link_id, user, "accepted")


@router.post("/links/{link_id}/reject")
def reject_link(link_id: int, project: OwnedProject, db: DbSession, user: CurrentUser) -> TraceLinkOut:
    """Reject a link; it is kept so the same target is never suggested again for this requirement."""
    return _decide(db, project, link_id, user, "rejected")


@router.post("/links", status_code=status.HTTP_201_CREATED)
def add_link(body: LinkCreate, project: OwnedProject, db: DbSession, user: CurrentUser) -> TraceLinkOut:
    """Link a requirement to a file, test, commit or PR of the analysed repository by hand (accepted at once)."""
    repo = require_repo(db, project)
    requirement = _approved(db, project, body.requirement_id)
    target = next(
        (t for t in all_targets(db, repo) if t.target_type == body.target_type and t.key == body.target_key), None
    )
    if target is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "That file, test, commit or pull request is not in the analysed repository.",
        )
    now = datetime.now(UTC)
    link = db.scalar(
        select(TraceLink).where(
            TraceLink.requirement_id == requirement.id,
            TraceLink.target_type == target.target_type,
            TraceLink.target_key == target.key,
        )
    )
    if link is None:
        link = TraceLink(
            project_id=project.id,
            repository_id=repo.id,
            requirement_id=requirement.id,
            link_type=TARGET_LINK_TYPE[target.target_type],
            target_type=target.target_type,
            target_key=target.key,
            origin="user",
            confidence=1.0,
            requirement_version=requirement.current.version,
        )
        db.add(link)
    elif link.status == "accepted" or (link.origin == "explicit" and link.status == "suggested"):
        raise HTTPException(status.HTTP_409_CONFLICT, "This link already exists.")
    # A link the AI dismissed or a person rejected becomes the person's own link
    link.origin, link.status = "user", "accepted"
    link.target_label, link.target_detail, link.target_line = target.label, target.detail, target.line
    link.reason, link.evidence, link.llm_checked = "Added by a person.", None, True
    link.decided_by_id, link.decided_at = user.id, now
    db.commit()
    db.refresh(link)
    return _link_result(db, repo, link)


@router.get("/targets")
def search_targets(
    project: OwnedProject,
    db: DbSession,
    q: Annotated[str, Query(max_length=200)] = "",
    target_type: TargetType | None = None,
) -> list[TargetOut]:
    """Files, tests, commits and PRs of the analysed repository whose name or title contains `q`."""
    repo = require_repo(db, project)
    needle = q.strip().lower()
    found: list[TargetOut] = []
    for t in all_targets(db, repo):
        if target_type is not None and t.target_type != target_type:
            continue
        if needle and needle not in t.label.lower() and needle not in (t.detail or "").lower():
            continue
        found.append(
            TargetOut(
                target_type=t.target_type,
                link_type=t.link_type,
                key=t.key,
                label=t.label,
                detail=t.detail,
                url=github_url(repo, t.target_type, t.key, t.line),
            )
        )
        if len(found) == MAX_TARGETS:
            break
    return found


@router.get("/export.csv")
def export_csv(project: OwnedProject, db: DbSession) -> Response:
    at = datetime.now(UTC)
    matrix = build_matrix(db, project_repo(db, project), approved_requirements(db, project.id))
    filename = export_filename(project.name, at, "csv", kind="traceability")
    return Response(
        build_csv(project.name, at, matrix),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
