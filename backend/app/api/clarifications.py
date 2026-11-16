"""Clarification questions, share links, client answers and proposals (Step 8A: AR-04, FR-09, FR-22).

Owner only (the project owner is the team lead), except `public_router`: the client's no-login page, which sees
only the questions of its link. Nothing is sent to the client by the app; the lead copies the link.
"""

from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, selectinload

from app.ai.clarify import (
    NoNewAnswers,
    generate_questions,
    link_state,
    propose_update,
    question_state,
    utcnow,
)
from app.ai.llm import LLMError
from app.api.clarification_views import link_out, question_out
from app.api.deps import LLM, CurrentUser, DbSession, OptionalLLM, OwnedProject, llm_error
from app.api.requirements import _LOAD, _detail, _get, add_user_version
from app.core.config import settings
from app.core.ratelimit import client_ip, enforce
from app.core.security import generate_reset_token, hash_secret
from app.models import (
    ClarificationAnswer,
    ClarificationLink,
    ClarificationProposal,
    ClarificationQuestion,
    Project,
    Requirement,
)
from app.schemas.clarification import (
    GenerateRequest,
    GenerateResult,
    LinkCreate,
    LinkCreated,
    LinkOut,
    ProposalAccept,
    PublicAnswers,
    PublicLink,
    PublicQuestion,
    PublicSubmitted,
    QuestionCreate,
    QuestionOut,
    QuestionUpdate,
    UnseenCount,
)
from app.schemas.requirement import RequirementDetail

router = APIRouter(prefix="/projects/{project_id}", tags=["clarifications"])
public_router = APIRouter(prefix="/public/clarify", tags=["clarifications"])

LINK_DAYS = 7
# Requests to the public page per minute and client address
PUBLIC_RATE_LIMIT = 30

_QUESTION_LOAD = (
    selectinload(ClarificationQuestion.requirement),
    selectinload(ClarificationQuestion.finding),
    selectinload(ClarificationQuestion.links),
    selectinload(ClarificationQuestion.answers),
)
_LINK_LOAD = (selectinload(ClarificationLink.questions).selectinload(ClarificationQuestion.requirement),)


def _question(db: Session, project: Project, question_id: int) -> ClarificationQuestion:
    question = db.scalar(
        select(ClarificationQuestion)
        .join(ClarificationQuestion.requirement)
        .where(ClarificationQuestion.id == question_id, Requirement.project_id == project.id)
        .options(*_QUESTION_LOAD)
    )
    if question is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Question not found")
    return question


def _requirements(db: Session, project: Project, ids: list[int] | None = None) -> list[Requirement]:
    query = select(Requirement).where(Requirement.project_id == project.id).options(*_LOAD).order_by(Requirement.number)
    if ids is not None:
        query = query.where(Requirement.id.in_(ids))
    return list(db.scalars(query).all())


# ---------- questions ----------


@router.post("/clarifications/generate", response_model=GenerateResult)
def generate_all(project: OwnedProject, db: DbSession, llm: LLM, body: GenerateRequest | None = None) -> GenerateResult:
    """Questions for every open requirement whose findings have no question yet (existing questions stay)."""
    ids = body.requirement_ids if body else None
    try:
        result = generate_questions(_requirements(db, project, ids), llm, replace=False)
    except LLMError as exc:
        raise llm_error(exc) from None
    db.commit()
    return result


@router.post("/requirements/{requirement_id}/questions/generate", response_model=GenerateResult)
def generate_for_requirement(requirement_id: int, project: OwnedProject, db: DbSession, llm: LLM) -> GenerateResult:
    """Write this requirement's questions again: untouched AI suggestions are replaced, the rest stay (rule 2)."""
    requirement = _get(db, project, requirement_id)
    if requirement.status in ("rejected", "merged"):
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"{requirement.code} is {requirement.status}")
    if requirement.approval is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"{requirement.code} is approved")
    try:
        result = generate_questions([requirement], llm, replace=True)
    except LLMError as exc:
        raise llm_error(exc) from None
    db.commit()
    return result


@router.get("/clarifications/questions", response_model=list[QuestionOut])
def list_questions(project: OwnedProject, db: DbSession, requirement_id: int | None = None) -> list[QuestionOut]:
    query = (
        select(ClarificationQuestion)
        .join(ClarificationQuestion.requirement)
        .where(Requirement.project_id == project.id)
        .options(*_QUESTION_LOAD)
        .order_by(Requirement.number, ClarificationQuestion.id)
    )
    if requirement_id is not None:
        query = query.where(ClarificationQuestion.requirement_id == requirement_id)
    at = utcnow()
    return [question_out(q, at) for q in db.scalars(query).all()]


@router.post(
    "/requirements/{requirement_id}/questions", response_model=QuestionOut, status_code=status.HTTP_201_CREATED
)
def add_question(
    requirement_id: int, body: QuestionCreate, project: OwnedProject, db: DbSession, user: CurrentUser
) -> QuestionOut:
    """A question written by the lead (origin user)."""
    requirement = _get(db, project, requirement_id)
    if body.finding_id is not None and body.finding_id not in {f.id for f in requirement.findings}:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Finding not found")
    question = ClarificationQuestion(
        finding_id=body.finding_id, text=body.text, origin="user", status="accepted", created_by_id=user.id
    )
    requirement.questions.append(question)
    db.commit()
    return question_out(_question(db, project, question.id), utcnow())


@router.patch("/clarifications/questions/{question_id}", response_model=QuestionOut)
def edit_question(question_id: int, body: QuestionUpdate, project: OwnedProject, db: DbSession) -> QuestionOut:
    question = _question(db, project, question_id)
    if question.answers:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="This question was already answered")
    if body.text != question.text:
        question.text = body.text
        if question.origin == "ai":
            question.status = "edited"
    db.commit()
    return question_out(_question(db, project, question_id), utcnow())


@router.delete("/clarifications/questions/{question_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_question(question_id: int, project: OwnedProject, db: DbSession) -> None:
    question = _question(db, project, question_id)
    state = question_state(question, utcnow())
    if state == "answered":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="This question was answered, so it is kept")
    if state == "shared":
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="This question is in an active link. Revoke the link first."
        )
    db.delete(question)
    db.commit()


# ---------- links ----------


@router.post("/clarifications/links", response_model=LinkCreated, status_code=status.HTTP_201_CREATED)
def create_link(body: LinkCreate, project: OwnedProject, db: DbSession, user: CurrentUser) -> LinkCreated:
    """A single-use link for the client with the chosen questions, valid for 7 days. Shown only in this answer."""
    ids = list(dict.fromkeys(body.question_ids))
    questions = db.scalars(
        select(ClarificationQuestion)
        .join(ClarificationQuestion.requirement)
        .where(ClarificationQuestion.id.in_(ids), Requirement.project_id == project.id)
        .options(*_QUESTION_LOAD)
    ).all()
    if len(questions) != len(ids):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Question not found")
    answered = [q for q in questions if q.answers]
    if answered:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"{len(answered)} of the chosen questions {'was' if len(answered) == 1 else 'were'} already answered",
        )
    token = generate_reset_token()
    by_id = {q.id: q for q in questions}
    link = ClarificationLink(
        project_id=project.id,
        token_hash=hash_secret(token),
        expires_at=utcnow() + timedelta(days=LINK_DAYS),
        created_by_id=user.id,
        questions=[by_id[i] for i in ids],
    )
    for question in questions:
        if question.status == "suggested":
            question.status = "accepted"  # the lead chose to send it
    db.add(link)
    db.commit()
    url = f"{settings.frontend_url.rstrip('/')}/clarify/{token}"
    return LinkCreated(**link_out(_link(db, project, link.id), utcnow()).model_dump(), url=url)


def _link(db: Session, project: Project, link_id: int) -> ClarificationLink:
    link = db.scalar(select(ClarificationLink).where(ClarificationLink.id == link_id).options(*_LINK_LOAD))
    if link is None or link.project_id != project.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Link not found")
    return link


@router.get("/clarifications/links", response_model=list[LinkOut])
def list_links(project: OwnedProject, db: DbSession) -> list[LinkOut]:
    links = db.scalars(
        select(ClarificationLink)
        .where(ClarificationLink.project_id == project.id)
        .options(*_LINK_LOAD)
        .order_by(ClarificationLink.id.desc())
    ).all()
    at = utcnow()
    return [link_out(link, at) for link in links]


@router.post("/clarifications/links/{link_id}/revoke", response_model=LinkOut)
def revoke_link(link_id: int, project: OwnedProject, db: DbSession) -> LinkOut:
    link = _link(db, project, link_id)
    if link.submitted_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="The client already answered through this link")
    if link.revoked_at is None:
        link.revoked_at = utcnow()
        db.commit()
    return link_out(link, utcnow())


# ---------- answers and proposals ----------


@router.get("/clarifications/unseen", response_model=UnseenCount)
def unseen_answers(project: OwnedProject, db: DbSession) -> UnseenCount:
    """Client answers the lead has not seen yet (the menu badge)."""
    count = db.scalar(
        select(func.count())
        .select_from(ClarificationAnswer)
        .join(ClarificationAnswer.requirement)
        .where(Requirement.project_id == project.id, ClarificationAnswer.seen_at.is_(None))
    )
    return UnseenCount(count=count or 0)


@router.post("/requirements/{requirement_id}/answers/seen", response_model=RequirementDetail)
def mark_answers_seen(requirement_id: int, project: OwnedProject, db: DbSession) -> RequirementDetail:
    requirement = _get(db, project, requirement_id)
    at = utcnow()
    changed = False
    for answer in requirement.answers:
        if answer.seen_at is None:
            answer.seen_at = at
            changed = True
    if changed:
        db.commit()
    return _detail(_get(db, project, requirement_id))


@router.post("/requirements/{requirement_id}/proposals", response_model=RequirementDetail)
def create_proposal(requirement_id: int, project: OwnedProject, db: DbSession, llm: LLM) -> RequirementDetail:
    """Ask the AI for an updated statement from the client's answers not used yet."""
    requirement = _get(db, project, requirement_id)
    if requirement.status in ("rejected", "merged"):
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"{requirement.code} is {requirement.status}")
    try:
        propose_update(requirement, llm)
    except NoNewAnswers:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="There are no new answers to use") from None
    except LLMError as exc:
        raise llm_error(exc) from None
    db.commit()
    return _detail(_get(db, project, requirement_id))


def _proposal(requirement: Requirement, proposal_id: int) -> ClarificationProposal:
    proposal = next((p for p in requirement.proposals if p.id == proposal_id), None)
    if proposal is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Proposal not found")
    if proposal.status != "suggested":
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"This proposal was already {proposal.status}")
    return proposal


@router.post("/requirements/{requirement_id}/proposals/{proposal_id}/accept", response_model=RequirementDetail)
def accept_proposal(
    requirement_id: int,
    proposal_id: int,
    project: OwnedProject,
    db: DbSession,
    user: CurrentUser,
    llm: OptionalLLM,
    body: ProposalAccept | None = None,
) -> RequirementDetail:
    """Save the proposed (or the lead's edited) statement as a new version by the lead, and validate it again."""
    requirement = _get(db, project, requirement_id)
    proposal = _proposal(requirement, proposal_id)
    if proposal.based_on_version != requirement.current.version:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="The requirement changed after this proposal. Propose an update again."
        )
    statement = body.statement if body and body.statement else proposal.statement
    if not statement:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="The AI found not enough information for a new statement. Write one to accept.",
        )
    version = add_user_version(db, project, requirement, user, {"statement": statement}, llm)
    proposal.status = "accepted" if statement == proposal.statement else "edited"
    proposal.decided_by_id = user.id
    proposal.decided_at = utcnow()
    proposal.version_id = version.id
    db.commit()
    return _detail(_get(db, project, requirement_id))


@router.post("/requirements/{requirement_id}/proposals/{proposal_id}/reject", response_model=RequirementDetail)
def reject_proposal(
    requirement_id: int, proposal_id: int, project: OwnedProject, db: DbSession, user: CurrentUser
) -> RequirementDetail:
    requirement = _get(db, project, requirement_id)
    proposal = _proposal(requirement, proposal_id)
    proposal.status = "rejected"
    proposal.decided_by_id = user.id
    proposal.decided_at = utcnow()
    db.commit()
    return _detail(_get(db, project, requirement_id))


# ---------- the client's page (no login) ----------

_CLOSED = {
    "expired": "This link has expired. Please ask the team for a new one.",
    "revoked": "This link is no longer active. Please ask the team for a new one.",
    "answered": "These questions were already answered through this link. Thank you!",
}


def _open_link(request: Request, db: Session, token: str) -> ClarificationLink:
    """The link of the token if it can still be answered: 404 unknown, 410 expired / revoked / answered."""
    enforce(f"clarify:{client_ip(request)}", PUBLIC_RATE_LIMIT, 60, "Too many requests.")
    link = db.scalar(
        select(ClarificationLink)
        .where(ClarificationLink.token_hash == hash_secret(token))
        .options(selectinload(ClarificationLink.questions), selectinload(ClarificationLink.project))
    )
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="This link is not valid. Please check the address.")
    state = link_state(link, utcnow())
    if state != "active":
        raise HTTPException(status.HTTP_410_GONE, detail=_CLOSED[state])
    return link


@public_router.get("/{token}", response_model=PublicLink)
def public_link(token: str, request: Request, db: DbSession) -> PublicLink:
    """Only the project name and the link's questions: no requirement codes, findings or other questions."""
    link = _open_link(request, db, token)
    return PublicLink(
        project_name=link.project.name,
        expires_at=link.expires_at,
        questions=[PublicQuestion(id=q.id, text=q.text) for q in link.questions],
    )


@public_router.post("/{token}/answers", response_model=PublicSubmitted)
def public_answers(token: str, body: PublicAnswers, request: Request, db: DbSession) -> PublicSubmitted:
    """Store the client's answers exactly as written and close the link (single use)."""
    link = _open_link(request, db, token)
    by_id = {q.id: q for q in link.questions}
    if any(a.question_id not in by_id for a in body.answers):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="An answer is for a question that is not on this page.")
    given = {a.question_id: a.text for a in body.answers if a.text.strip()}
    if not given:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Please answer at least one question.")
    at = utcnow()
    # Closing the link first, only if still open, so two submissions at the same time cannot both get in
    closed = db.execute(
        update(ClarificationLink)
        .where(
            ClarificationLink.id == link.id,
            ClarificationLink.submitted_at.is_(None),
            ClarificationLink.revoked_at.is_(None),
        )
        .values(submitted_at=at)
        .execution_options(synchronize_session=False)
    )
    if closed.rowcount != 1:
        db.rollback()
        raise HTTPException(status.HTTP_410_GONE, detail=_CLOSED["answered"])
    for question_id, text in given.items():
        question = by_id[question_id]
        db.add(
            ClarificationAnswer(
                question_id=question.id, requirement_id=question.requirement_id, link_id=link.id, text=text, answered_at=at
            )
        )
    db.commit()
    return PublicSubmitted(answered=len(given))
