"""Requirements of a project: AI extraction, list, edit (new version), accept / reject / merge, undo / restore,
validation (rule-based checks and the pair checks: duplicates, conflicts, change requests).

The pair checks run again for every requirement whose text or status changes, so a merged or rejected one stops
being compared and accepting one can turn a newer conflict into a change request.

Owner only.
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import CurrentUser, DbSession, OptionalLLM, OwnedProject
from app.models import Project, Requirement, RequirementSource, RequirementVersion, ValidationFinding
from app.nlp.extraction import extract_requirements
from app.nlp.pair_checks import check_similarity
from app.nlp.validate import validate_project, validate_requirement
from app.schemas.extraction import ExtractResult
from app.schemas.requirement import (
    BulkRequest,
    BulkResult,
    BulkSkipped,
    ExtractRequest,
    MergeRequest,
    RejectRequest,
    RequirementDetail,
    RequirementOut,
    RequirementRef,
    RequirementSourceDetail,
    RequirementSourceOut,
    RequirementStatus,
    RequirementType,
    RequirementUpdate,
    RequirementVersionOut,
    ValidateResult,
    ValidationFindingOut,
)

router = APIRouter(prefix="/projects/{project_id}/requirements", tags=["requirements"])

NO_LLM_CONFIGURED = "No AI model is configured. Set GROQ_API_KEY, or LLM_MODEL and LLM_API_KEY, in backend/.env."

_LOAD = (
    selectinload(Requirement.versions).selectinload(RequirementVersion.created_by),
    selectinload(Requirement.sources).selectinload(RequirementSource.message),
    selectinload(Requirement.findings),
)


def _get(db: Session, project: Project, requirement_id: int) -> Requirement:
    requirement = db.scalar(select(Requirement).where(Requirement.id == requirement_id).options(*_LOAD))
    if requirement is None or requirement.project_id != project.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Requirement not found")
    return requirement


def _source(s: RequirementSource) -> dict:
    return {
        "message_id": s.message_id,
        "source_sentence": s.source_sentence,
        "source": s.message.source,
        "channel": s.message.channel,
        "author": s.message.author,
        "sent_at": s.message.sent_at,
        "merged_from_id": s.merged_from_id,
    }


def _change_request_for(r: Requirement) -> RequirementRef | None:
    if r.status in ("rejected", "merged"):
        return None
    finding = next(
        (f for f in r.findings if f.category == "change_request" and f.status != "rejected" and f.related_code),
        None,
    )
    return RequirementRef(id=finding.related_requirement_id, code=finding.related_code) if finding else None


def _out(r: Requirement) -> RequirementOut:
    return RequirementOut(
        id=r.id,
        code=r.code,
        origin=r.origin,
        status=r.status,
        merged_into_id=r.merged_into_id,
        reject_reason=r.reject_reason if r.status == "rejected" else None,
        created_at=r.created_at,
        updated_at=r.updated_at,
        current=RequirementVersionOut.model_validate(r.current),
        version_count=len(r.versions),
        sources=[RequirementSourceOut(**_source(s)) for s in r.sources],
        quality_score=r.quality_score,
        quality_failed=r.quality_failed,
        module_name=r.module_name,
        module_score=r.module_score,
        open_findings=r.open_findings,
        has_issues=r.has_issues,
        change_request_for=_change_request_for(r),
    )


def _detail(r: Requirement) -> RequirementDetail:
    return RequirementDetail(
        **_out(r).model_dump(exclude={"sources"}),
        sources=[RequirementSourceDetail(**_source(s), message_text=s.message.text) for s in r.sources],
        versions=[RequirementVersionOut.model_validate(v) for v in r.versions],
        findings=[ValidationFindingOut.model_validate(f) for f in r.findings],
    )


def _ensure_changeable(requirement: Requirement, *, editing: bool) -> None:
    """Merged requirements are closed; rejected ones can change status but not get new content."""
    if requirement.status == "merged":
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail=f"{requirement.code} was merged into another requirement"
        )
    if editing and requirement.status == "rejected":
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"{requirement.code} is rejected and cannot be edited")


@router.post("/extract", response_model=ExtractResult)
def extract(
    project: OwnedProject, db: DbSession, llm: OptionalLLM, body: ExtractRequest | None = None
) -> ExtractResult:
    """Extract requirements from messages not read yet (or again, with force / message_ids).

    Always gives a result: without a configured or answering AI model the rule-based fallback is used.
    """
    body = body or ExtractRequest()
    result = extract_requirements(
        db,
        project,
        llm,
        force=body.force,
        message_ids=body.message_ids,
        llm_unavailable_reason=None if llm else NO_LLM_CONFIGURED,
    )
    result.similarity_unavailable_reason = validate_project(
        db, project, only_new=True
    ).similarity_unavailable_reason
    db.commit()
    return result


@router.post("/validate", response_model=ValidateResult)
def validate(project: OwnedProject, db: DbSession) -> ValidateResult:
    """Run every check on every requirement, for example after the modules changed."""
    result = validate_project(db, project)
    db.commit()
    return result


@router.post("/bulk", response_model=BulkResult)
def bulk_review(body: BulkRequest, project: OwnedProject, db: DbSession) -> BulkResult:
    """Accept, reject or undo many at once, in one transaction.

    An id outside this project fails the whole call and changes nothing. A requirement the action does not
    apply to (merged, already in that status, nothing to undo) is skipped and reported.
    """
    ids = list(dict.fromkeys(body.ids))
    found = {
        r.id: r
        for r in db.scalars(
            select(Requirement).where(Requirement.project_id == project.id, Requirement.id.in_(ids)).options(*_LOAD)
        ).all()
    }
    if len(found) != len(ids):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Requirement not found")

    new_status = {"accept": "accepted", "reject": "rejected"}.get(body.action)
    updated: list[Requirement] = []
    skipped: list[BulkSkipped] = []
    for requirement in (found[rid] for rid in ids):
        if requirement.status == new_status:
            skipped.append(BulkSkipped(id=requirement.id, code=requirement.code, reason=f"Already {new_status}"))
            continue
        try:
            if new_status:
                _review(requirement, new_status, body.reason)
            else:
                _undo(requirement)
        except HTTPException as exc:
            skipped.append(BulkSkipped(id=requirement.id, code=requirement.code, reason=exc.detail))
        else:
            updated.append(requirement)
    check_similarity(db, project, [r.id for r in updated])
    db.commit()
    return BulkResult(updated=[_out(_get(db, project, r.id)) for r in updated], skipped=skipped)


@router.get("", response_model=list[RequirementOut])
def list_requirements(
    project: OwnedProject,
    db: DbSession,
    status_: Annotated[RequirementStatus | None, Query(alias="status")] = None,
    type_: Annotated[RequirementType | None, Query(alias="type")] = None,
) -> list[RequirementOut]:
    query = select(Requirement).where(Requirement.project_id == project.id).options(*_LOAD).order_by(Requirement.number)
    if status_:
        query = query.where(Requirement.status == status_)
    items = [_out(r) for r in db.scalars(query).all()]
    # The type lives in the current version
    return [r for r in items if r.current.type == type_] if type_ else items


@router.get("/findings", response_model=list[ValidationFindingOut])
def list_findings(project: OwnedProject, db: DbSession) -> list[ValidationFinding]:
    """All findings of the project, by requirement."""
    return list(
        db.scalars(
            select(ValidationFinding)
            .join(ValidationFinding.requirement)
            .where(Requirement.project_id == project.id)
            .order_by(Requirement.number, ValidationFinding.id)
        )
    )


@router.get("/{requirement_id}", response_model=RequirementDetail)
def get_requirement(requirement_id: int, project: OwnedProject, db: DbSession) -> RequirementDetail:
    return _detail(_get(db, project, requirement_id))


@router.patch("/{requirement_id}", response_model=RequirementDetail)
def edit_requirement(
    requirement_id: int, body: RequirementUpdate, project: OwnedProject, db: DbSession, user: CurrentUser
) -> RequirementDetail:
    """Save the change as a new version (the old versions stay as they are) and mark the requirement edited."""
    requirement = _get(db, project, requirement_id)
    _ensure_changeable(requirement, editing=True)
    current = requirement.current
    changes = body.model_dump(exclude_unset=True)
    new_type = changes.get("type") or current.type
    category = changes["nfr_category"] if "nfr_category" in changes else current.nfr_category
    if new_type == "functional":
        category = None
    elif category is None:
        category = "other"
    requirement.versions.append(
        RequirementVersion(
            version=current.version + 1,
            statement=changes.get("statement") or current.statement,
            type=new_type,
            nfr_category=category,
            actor=changes["actor"] if changes.get("actor") is not None else current.actor,
            confidence=None,
            origin="user",
            created_by_id=user.id,
        )
    )
    requirement.status = "edited"
    requirement.previous_status = None  # an undo must not jump over this edit
    validate_requirement(requirement, project)
    db.flush()  # the new version needs its id for the embedding
    check_similarity(db, project, [requirement.id])
    db.commit()
    return _detail(_get(db, project, requirement_id))


def _review(requirement: Requirement, new_status: str, reason: str | None = None) -> None:
    """Accept or reject. Repeating one changes no status, so undo still goes to the real previous status."""
    _ensure_changeable(requirement, editing=False)
    if new_status == "rejected":
        requirement.reject_reason = reason or None
    if requirement.status != new_status:
        requirement.previous_status = requirement.status
        requirement.status = new_status


def _undo(requirement: Requirement) -> None:
    _ensure_changeable(requirement, editing=False)
    if requirement.previous_status is None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"{requirement.code} has nothing to undo")
    requirement.status = requirement.previous_status
    requirement.previous_status = None


@router.post("/{requirement_id}/accept", response_model=RequirementDetail)
def accept_requirement(requirement_id: int, project: OwnedProject, db: DbSession) -> RequirementDetail:
    _review(_get(db, project, requirement_id), "accepted")
    check_similarity(db, project, [requirement_id])
    db.commit()
    return _detail(_get(db, project, requirement_id))


@router.post("/{requirement_id}/reject", response_model=RequirementDetail)
def reject_requirement(
    requirement_id: int, project: OwnedProject, db: DbSession, body: RejectRequest | None = None
) -> RequirementDetail:
    _review(_get(db, project, requirement_id), "rejected", body.reason if body else None)
    check_similarity(db, project, [requirement_id])
    db.commit()
    return _detail(_get(db, project, requirement_id))


@router.post("/{requirement_id}/undo", response_model=RequirementDetail)
def undo_review(requirement_id: int, project: OwnedProject, db: DbSession) -> RequirementDetail:
    """Take back the last accept / reject: the status returns to what it was."""
    _undo(_get(db, project, requirement_id))
    check_similarity(db, project, [requirement_id])
    db.commit()
    return _detail(_get(db, project, requirement_id))


def _take_back_merged_sources(db: Session, project: Project, requirement: Requirement) -> None:
    """Remove the source rows a merge copied from `requirement` to its target.

    A row stays when another requirement still merged into that target has the same sentence.
    """
    if requirement.merged_into_id is None:
        return
    target = _get(db, project, requirement.merged_into_id)
    still_merged = db.scalars(
        select(Requirement)
        .where(Requirement.merged_into_id == target.id, Requirement.id != requirement.id)
        .options(selectinload(Requirement.sources))
    ).all()
    for row in [s for s in target.sources if s.merged_from_id == requirement.id]:
        key = (row.message_id, row.source_sentence)
        holder = next(
            (r for r in still_merged if key in {(s.message_id, s.source_sentence) for s in r.sources}), None
        )
        if holder is None:
            target.sources.remove(row)
        else:
            row.merged_from_id = holder.id


@router.post("/{requirement_id}/restore", response_model=RequirementDetail)
def restore_requirement(requirement_id: int, project: OwnedProject, db: DbSession) -> RequirementDetail:
    """Bring a rejected or merged requirement back to "suggested" for a new review."""
    requirement = _get(db, project, requirement_id)
    if requirement.status not in ("rejected", "merged"):
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail=f"{requirement.code} is not rejected or merged, so it cannot be restored"
        )
    target = None
    if requirement.status == "merged":
        _take_back_merged_sources(db, project, requirement)
        target = _get(db, project, requirement.merged_into_id) if requirement.merged_into_id else None
    requirement.status = "suggested"
    requirement.merged_into_id = None
    requirement.reject_reason = None
    requirement.previous_status = None
    if target is not None:  # it lost source sentences
        validate_requirement(target, project)
    check_similarity(db, project, [requirement_id])
    db.commit()
    return _detail(_get(db, project, requirement_id))


@router.post("/{requirement_id}/merge", response_model=RequirementDetail)
def merge_requirements(
    requirement_id: int, body: MergeRequest, project: OwnedProject, db: DbSession
) -> RequirementDetail:
    """Fold other requirements into this one: their source sentences are added here, they become "merged"."""
    target = _get(db, project, requirement_id)
    _ensure_changeable(target, editing=True)
    other_ids = list(dict.fromkeys(body.other_ids))
    if requirement_id in other_ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="A requirement cannot be merged into itself")
    others = [_get(db, project, rid) for rid in other_ids]
    for other in others:
        _ensure_changeable(other, editing=False)

    have = {(s.message_id, s.source_sentence) for s in target.sources}
    for other in others:
        for s in other.sources:
            if (s.message_id, s.source_sentence) not in have:
                target.sources.append(
                    RequirementSource(
                        message_id=s.message_id, source_sentence=s.source_sentence, merged_from_id=other.id
                    )
                )
                have.add((s.message_id, s.source_sentence))
        other.status = "merged"
        other.merged_into_id = target.id
        other.previous_status = None
    target.previous_status = None
    validate_requirement(target, project)  # it gained source sentences
    check_similarity(db, project, [target.id, *other_ids])
    db.commit()
    return _detail(_get(db, project, requirement_id))
