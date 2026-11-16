"""Acceptance criteria and Given/When/Then test cases of approved requirements (Step 8B: AR-05, FR-10).

The AI writes suggestions; the lead accepts, edits (a new version, rule 6) or rejects them, or writes their own.
Export lists approved requirements with their accepted criteria and test cases as CSV or PDF. Owner only.
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.ai.llm import LLMError
from app.ai.test_cases import generate_test_cases, needs_generation
from app.api.deps import LLM, CurrentUser, DbSession, OwnedProject, llm_error
from app.models import (
    AcceptanceCriterion,
    AcceptanceCriterionVersion,
    Project,
    Requirement,
    TestCase,
    TestCaseVersion,
)
from app.reports.test_case_export import build_csv, build_pdf, export_filename, export_rows
from app.schemas.test_case import (
    CriterionIn,
    CriterionOut,
    CriterionVersionOut,
    GenerateTestCasesResult,
    ItemStatus,
    RequirementSummary,
    TestCaseGroup,
    TestCaseIn,
    TestCaseOut,
    TestCaseType,
    TestCaseVersionOut,
)

router = APIRouter(prefix="/projects/{project_id}", tags=["test cases"])

_LOAD = (
    selectinload(Requirement.versions),
    selectinload(Requirement.sources),
    selectinload(Requirement.approvals),
    selectinload(Requirement.criteria)
    .selectinload(AcceptanceCriterion.versions)
    .selectinload(AcceptanceCriterionVersion.created_by),
    selectinload(Requirement.test_cases).selectinload(TestCase.versions).selectinload(TestCaseVersion.created_by),
)


# ---------- loading and output ----------


def _requirements(db: Session, project: Project) -> list[Requirement]:
    query = select(Requirement).where(Requirement.project_id == project.id).options(*_LOAD).order_by(Requirement.number)
    return list(db.scalars(query).all())


def _requirement(db: Session, project: Project, requirement_id: int) -> Requirement:
    requirement = db.scalar(select(Requirement).where(Requirement.id == requirement_id).options(*_LOAD))
    if requirement is None or requirement.project_id != project.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Requirement not found")
    return requirement


def _item_requirement(db: Session, project: Project, model: type, item_id: int, what: str) -> Requirement:
    requirement_id = db.scalar(
        select(model.requirement_id)
        .join(Requirement, Requirement.id == model.requirement_id)
        .where(model.id == item_id, Requirement.project_id == project.id)
    )
    if requirement_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"{what} not found")
    return _requirement(db, project, requirement_id)


def _criterion(db: Session, project: Project, criterion_id: int) -> AcceptanceCriterion:
    requirement = _item_requirement(db, project, AcceptanceCriterion, criterion_id, "Acceptance criterion")
    return next(c for c in requirement.criteria if c.id == criterion_id)


def _test_case(db: Session, project: Project, test_case_id: int) -> TestCase:
    requirement = _item_requirement(db, project, TestCase, test_case_id, "Test case")
    return next(t for t in requirement.test_cases if t.id == test_case_id)


def _criterion_out(c: AcceptanceCriterion, requirement: Requirement) -> CriterionOut:
    return CriterionOut(
        id=c.id,
        requirement_id=c.requirement_id,
        code=c.code,
        text=c.current.text,
        origin=c.origin,
        status=c.status,
        needs_review=c.needs_review,
        requirement_version=c.requirement_version,
        ai_model=c.ai_model,
        test_case_count=sum(1 for t in requirement.live_test_cases if t.current.criterion_id == c.id),
        versions=[CriterionVersionOut.model_validate(v) for v in c.versions],
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


def _test_case_out(t: TestCase, criteria: dict[int, AcceptanceCriterion]) -> TestCaseOut:
    v = t.current
    criterion = criteria.get(v.criterion_id) if v.criterion_id is not None else None
    return TestCaseOut(
        id=t.id,
        requirement_id=t.requirement_id,
        code=t.code,
        title=v.title,
        type=v.type,
        given=v.given,
        when=v.when,
        then=v.then,
        criterion_id=criterion.id if criterion else None,
        criterion_code=criterion.code if criterion else None,
        criterion_status=criterion.status if criterion else None,
        origin=t.origin,
        status=t.status,
        needs_review=t.needs_review,
        requirement_version=t.requirement_version,
        ai_model=t.ai_model,
        versions=[TestCaseVersionOut.model_validate(x) for x in t.versions],
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


def _group(
    r: Requirement,
    criteria: list[AcceptanceCriterion] | None = None,
    tests: list[TestCase] | None = None,
) -> TestCaseGroup:
    by_id = {c.id: c for c in r.criteria}
    return TestCaseGroup(
        requirement=RequirementSummary(
            id=r.id,
            code=r.code,
            statement=r.current.statement,
            version=r.current.version,
            status=r.status,
            approved=r.approval is not None,
        ),
        criteria=[_criterion_out(c, r) for c in (r.criteria if criteria is None else criteria)],
        test_cases=[_test_case_out(t, by_id) for t in (r.test_cases if tests is None else tests)],
    )


def _fresh_group(db: Session, project: Project, requirement_id: int) -> TestCaseGroup:
    db.expire_all()  # a regeneration may have removed rows the session still holds
    return _group(_requirement(db, project, requirement_id))


# ---------- rules ----------


def _ensure_open(requirement: Requirement) -> None:
    if requirement.status in ("rejected", "merged"):
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"{requirement.code} is {requirement.status}")


def _ensure_approved(requirement: Requirement) -> None:
    _ensure_open(requirement)
    if requirement.approval is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Only approved requirements get test cases. Approve {requirement.code} first.",
        )


def _linkable(requirement: Requirement, criterion_id: int | None) -> AcceptanceCriterion | None:
    if criterion_id is None:
        return None
    criterion = next((c for c in requirement.criteria if c.id == criterion_id), None)
    if criterion is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Acceptance criterion not found")
    if criterion.status == "rejected":
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"{criterion.code} is rejected")
    return criterion


def _decide(item: AcceptanceCriterion | TestCase, new_status: str) -> None:
    item.status = new_status
    item.needs_review = False


# ---------- generation ----------


@router.post("/requirements/{requirement_id}/test-cases/generate", response_model=GenerateTestCasesResult)
def generate_for_requirement(
    requirement_id: int, project: OwnedProject, db: DbSession, llm: LLM
) -> GenerateTestCasesResult:
    """Write this requirement's criteria and test cases again: untouched AI suggestions are replaced (rule 2)."""
    requirement = _requirement(db, project, requirement_id)
    _ensure_approved(requirement)
    try:
        result = generate_test_cases([requirement], llm)
    except LLMError as exc:
        raise llm_error(exc) from None
    db.commit()
    return result


@router.post("/test-cases/generate", response_model=GenerateTestCasesResult)
def generate_all(project: OwnedProject, db: DbSession, llm: LLM) -> GenerateTestCasesResult:
    """Approved requirements with no test cases yet, or whose untouched AI suggestions need review."""
    try:
        result = generate_test_cases([r for r in _requirements(db, project) if needs_generation(r)], llm)
    except LLMError as exc:
        raise llm_error(exc) from None
    db.commit()
    return result


# ---------- list ----------


@router.get("/test-cases", response_model=list[TestCaseGroup])
def list_test_cases(
    project: OwnedProject,
    db: DbSession,
    status_: Annotated[ItemStatus | None, Query(alias="status")] = None,
    type_: Annotated[TestCaseType | None, Query(alias="type")] = None,
    requirement_id: int | None = None,
    needs_review: bool | None = None,
) -> list[TestCaseGroup]:
    """Criteria and test cases grouped by requirement: approved requirements and any that have items.

    With a filter, only matching items are listed and groups with none are left out. The type filter applies to
    test cases only.
    """
    filtered = status_ is not None or type_ is not None or needs_review is not None
    groups = []
    for r in _requirements(db, project):
        if requirement_id is not None and r.id != requirement_id:
            continue
        if r.approval is None and not r.criteria and not r.test_cases:
            continue
        criteria = [
            c
            for c in r.criteria
            if (status_ is None or c.status == status_) and (needs_review is None or c.needs_review == needs_review)
        ]
        tests = [
            t
            for t in r.test_cases
            if (status_ is None or t.status == status_)
            and (type_ is None or t.current.type == type_)
            and (needs_review is None or t.needs_review == needs_review)
        ]
        if filtered and not tests and not (criteria and type_ is None):
            continue
        groups.append(_group(r, criteria if type_ is None else [], tests))
    return groups


# ---------- test cases ----------


def _test_case_version(number: int, body: TestCaseIn, criterion: AcceptanceCriterion | None, user_id: int):
    return TestCaseVersion(
        version=number,
        title=body.title,
        type=body.type,
        given=body.given,
        when=body.when,
        then=body.then,
        criterion=criterion,
        origin="user",
        created_by_id=user_id,
    )


@router.post(
    "/requirements/{requirement_id}/test-cases", response_model=TestCaseGroup, status_code=status.HTTP_201_CREATED
)
def add_test_case(
    requirement_id: int, body: TestCaseIn, project: OwnedProject, db: DbSession, user: CurrentUser
) -> TestCaseGroup:
    """A test case written by the lead (origin user, accepted)."""
    requirement = _requirement(db, project, requirement_id)
    _ensure_approved(requirement)
    criterion = _linkable(requirement, body.criterion_id)
    requirement.test_case_counter = (requirement.test_case_counter or 0) + 1
    requirement.test_cases.append(
        TestCase(
            number=requirement.test_case_counter,
            origin="user",
            status="accepted",
            requirement_version=requirement.current.version,
            created_by_id=user.id,
            versions=[_test_case_version(1, body, criterion, user.id)],
        )
    )
    db.commit()
    return _fresh_group(db, project, requirement_id)


@router.patch("/test-cases/{test_case_id}", response_model=TestCaseGroup)
def edit_test_case(
    test_case_id: int, body: TestCaseIn, project: OwnedProject, db: DbSession, user: CurrentUser
) -> TestCaseGroup:
    """Save the lead's change as a new version (rule 6): status edited, no longer "needs review"."""
    test = _test_case(db, project, test_case_id)
    requirement = test.requirement
    _ensure_open(requirement)
    criterion = _linkable(requirement, body.criterion_id)
    v = test.current
    unchanged = (v.title, v.type, v.given, v.when, v.then, v.criterion_id) == (
        body.title,
        body.type,
        body.given,
        body.when,
        body.then,
        body.criterion_id,
    )
    if not unchanged:
        test.versions.append(_test_case_version(v.version + 1, body, criterion, user.id))
        test.requirement_version = requirement.current.version
        _decide(test, "edited")
        _accept_criterion_of(test)
        db.commit()
    return _fresh_group(db, project, requirement.id)


def _accept_criterion_of(test: TestCase) -> None:
    """Accepting or editing a test case also accepts the criterion it verifies, when that is still a suggestion."""
    criterion = test.current.criterion
    if criterion is not None and criterion.status == "suggested":
        _decide(criterion, "accepted")


@router.post("/test-cases/{test_case_id}/accept", response_model=TestCaseGroup)
def accept_test_case(test_case_id: int, project: OwnedProject, db: DbSession) -> TestCaseGroup:
    test = _test_case(db, project, test_case_id)
    _ensure_open(test.requirement)
    _decide(test, "accepted")
    test.requirement_version = test.requirement.current.version
    _accept_criterion_of(test)
    db.commit()
    return _fresh_group(db, project, test.requirement_id)


@router.post("/test-cases/{test_case_id}/reject", response_model=TestCaseGroup)
def reject_test_case(test_case_id: int, project: OwnedProject, db: DbSession) -> TestCaseGroup:
    test = _test_case(db, project, test_case_id)
    _ensure_open(test.requirement)
    _decide(test, "rejected")
    db.commit()
    return _fresh_group(db, project, test.requirement_id)


# ---------- criteria ----------


@router.post(
    "/requirements/{requirement_id}/criteria", response_model=TestCaseGroup, status_code=status.HTTP_201_CREATED
)
def add_criterion(
    requirement_id: int, body: CriterionIn, project: OwnedProject, db: DbSession, user: CurrentUser
) -> TestCaseGroup:
    """An acceptance criterion written by the lead (origin user, accepted)."""
    requirement = _requirement(db, project, requirement_id)
    _ensure_approved(requirement)
    requirement.criterion_counter = (requirement.criterion_counter or 0) + 1
    requirement.criteria.append(
        AcceptanceCriterion(
            number=requirement.criterion_counter,
            origin="user",
            status="accepted",
            requirement_version=requirement.current.version,
            created_by_id=user.id,
            versions=[AcceptanceCriterionVersion(version=1, text=body.text, origin="user", created_by_id=user.id)],
        )
    )
    db.commit()
    return _fresh_group(db, project, requirement_id)


@router.patch("/criteria/{criterion_id}", response_model=TestCaseGroup)
def edit_criterion(
    criterion_id: int, body: CriterionIn, project: OwnedProject, db: DbSession, user: CurrentUser
) -> TestCaseGroup:
    criterion = _criterion(db, project, criterion_id)
    requirement = criterion.requirement
    _ensure_open(requirement)
    current = criterion.current
    if body.text != current.text:
        criterion.versions.append(
            AcceptanceCriterionVersion(
                version=current.version + 1, text=body.text, origin="user", created_by_id=user.id
            )
        )
        criterion.requirement_version = requirement.current.version
        _decide(criterion, "edited")
        db.commit()
    return _fresh_group(db, project, requirement.id)


@router.post("/criteria/{criterion_id}/accept", response_model=TestCaseGroup)
def accept_criterion(criterion_id: int, project: OwnedProject, db: DbSession) -> TestCaseGroup:
    criterion = _criterion(db, project, criterion_id)
    _ensure_open(criterion.requirement)
    _decide(criterion, "accepted")
    criterion.requirement_version = criterion.requirement.current.version
    db.commit()
    return _fresh_group(db, project, criterion.requirement_id)


@router.post("/criteria/{criterion_id}/reject", response_model=TestCaseGroup)
def reject_criterion(criterion_id: int, project: OwnedProject, db: DbSession) -> TestCaseGroup:
    """Refused while test cases that are not rejected verify it: they would lose what they check."""
    criterion = _criterion(db, project, criterion_id)
    requirement = criterion.requirement
    _ensure_open(requirement)
    users = [t.code for t in requirement.live_test_cases if t.current.criterion_id == criterion.id]
    if users:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"{', '.join(users)} {'verifies' if len(users) == 1 else 'verify'} {criterion.code}. "
            "Reject them or link them to another criterion first.",
        )
    _decide(criterion, "rejected")
    db.commit()
    return _fresh_group(db, project, requirement.id)


# ---------- export (FR-10) ----------


@router.get("/test-cases/export.csv")
def export_csv(project: OwnedProject, db: DbSession) -> Response:
    at = datetime.now(UTC)
    content = build_csv(project.name, at, export_rows(_requirements(db, project)))
    return Response(
        content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{export_filename(project.name, at, "csv")}"'},
    )


@router.get("/test-cases/export.pdf")
def export_pdf(project: OwnedProject, db: DbSession) -> Response:
    at = datetime.now(UTC)
    content = build_pdf(project.name, at, export_rows(_requirements(db, project)))
    return Response(
        content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{export_filename(project.name, at, "pdf")}"'},
    )
