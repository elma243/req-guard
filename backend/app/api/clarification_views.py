"""Turns clarification rows into API schemas. Shared by the requirements and clarifications routers."""

from datetime import datetime

from app.ai.clarify import link_state, question_state
from app.models import ClarificationAnswer, ClarificationLink, ClarificationQuestion
from app.schemas.clarification import AnswerOut, LinkOut, LinkQuestion, QuestionOut


def question_out(q: ClarificationQuestion, at: datetime) -> QuestionOut:
    finding = q.finding
    return QuestionOut(
        id=q.id,
        requirement_id=q.requirement_id,
        requirement_code=q.requirement.code,
        finding_id=q.finding_id,
        finding_category=finding.category if finding else None,
        finding_reason=finding.reason if finding else None,
        text=q.text,
        origin=q.origin,
        status=q.status,
        ai_model=q.ai_model,
        state=question_state(q, at),
        answer_count=len(q.answers),
        created_at=q.created_at,
        updated_at=q.updated_at,
    )


def answer_out(a: ClarificationAnswer) -> AnswerOut:
    return AnswerOut(
        id=a.id,
        question_id=a.question_id,
        question_text=a.question.text,
        requirement_id=a.requirement_id,
        link_id=a.link_id,
        text=a.text,
        answered_at=a.answered_at,
        seen_at=a.seen_at,
    )


def link_out(link: ClarificationLink, at: datetime) -> LinkOut:
    return LinkOut(
        id=link.id,
        state=link_state(link, at),
        created_at=link.created_at,
        expires_at=link.expires_at,
        revoked_at=link.revoked_at,
        submitted_at=link.submitted_at,
        questions=[
            LinkQuestion(id=q.id, requirement_id=q.requirement_id, requirement_code=q.requirement.code, text=q.text)
            for q in link.questions
        ],
    )
