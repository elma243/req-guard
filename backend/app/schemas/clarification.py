"""Schemas for clarification questions, share links, client answers, proposals and approvals (Step 8A)."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.schemas.common import UtcDatetime

# Longest question the app keeps; the AI's longer ones are dropped (a cut question would read badly)
MAX_QUESTION_CHARS = 300
# Longest answer the client can send per question
MAX_ANSWER_CHARS = 2000
# Most questions in one link
MAX_LINK_QUESTIONS = 20

QuestionText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_QUESTION_CHARS)]
QuestionState = Literal["draft", "shared", "answered"]
LinkState = Literal["active", "expired", "revoked", "answered"]


# ---------- LLM answers ----------


class GeneratedQuestion(BaseModel):
    finding_id: int | None = None
    # Generous here so one long question does not fail the whole answer; the code drops what is too long
    text: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)]


class RequirementQuestions(BaseModel):
    requirement_id: int
    not_enough_information: bool = False
    questions: list[GeneratedQuestion] = Field(default_factory=list, max_length=10)


class GeneratedQuestions(BaseModel):
    requirements: list[RequirementQuestions] = Field(max_length=50)


class ProposalAnswer(BaseModel):
    not_enough_information: bool = False
    statement: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] = ""
    reason: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] = ""


# ---------- questions ----------


class QuestionOut(BaseModel):
    id: int
    requirement_id: int
    requirement_code: str
    # The finding this question resolves; None when written without one, or when a re-validation removed it
    finding_id: int | None
    finding_category: str | None
    finding_reason: str | None
    text: str
    origin: Literal["ai", "user"]
    status: Literal["suggested", "edited", "accepted", "rejected"]
    ai_model: str | None
    # draft: in no active link and not answered; shared: in an active link; answered: the client answered it
    state: QuestionState
    answer_count: int
    created_at: UtcDatetime
    updated_at: UtcDatetime


class QuestionCreate(BaseModel):
    text: QuestionText
    finding_id: int | None = None


class QuestionUpdate(BaseModel):
    text: QuestionText


class GenerateItem(BaseModel):
    requirement_id: int
    code: str
    created: int
    # The AI could not write a good question from what it was given
    not_enough_information: bool


class GenerateResult(BaseModel):
    created: int
    items: list[GenerateItem]
    # Set when a later batch failed: the earlier batches' questions were kept
    error: str | None = None


class GenerateRequest(BaseModel):
    # None = every requirement with open findings that need a question
    requirement_ids: list[int] | None = Field(default=None, max_length=500)


# ---------- links ----------


class LinkCreate(BaseModel):
    question_ids: list[int] = Field(min_length=1, max_length=MAX_LINK_QUESTIONS)


class LinkQuestion(BaseModel):
    id: int
    requirement_id: int
    requirement_code: str
    text: str


class LinkOut(BaseModel):
    id: int
    state: LinkState
    created_at: UtcDatetime
    expires_at: UtcDatetime
    revoked_at: UtcDatetime | None
    submitted_at: UtcDatetime | None
    questions: list[LinkQuestion]


class LinkCreated(LinkOut):
    # The only time the link is shown: just a hash of its token is stored
    url: str


class PublicQuestion(BaseModel):
    id: int
    text: str


class PublicLink(BaseModel):
    """What the client sees: the project name and the chosen questions, nothing else."""

    project_name: str
    expires_at: UtcDatetime
    questions: list[PublicQuestion]


class PublicAnswerIn(BaseModel):
    question_id: int
    # Stored exactly as received (rule 10), so not stripped
    text: str = Field(max_length=MAX_ANSWER_CHARS)


class PublicAnswers(BaseModel):
    answers: list[PublicAnswerIn] = Field(min_length=1, max_length=MAX_LINK_QUESTIONS)


class PublicSubmitted(BaseModel):
    answered: int


# ---------- answers and proposals ----------


class AnswerOut(BaseModel):
    id: int
    question_id: int
    question_text: str
    requirement_id: int
    link_id: int | None
    text: str
    answered_at: UtcDatetime
    seen_at: UtcDatetime | None


class ProposalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    requirement_id: int
    based_on_version: int
    answer_ids: list[int]
    statement: str | None
    reason: str
    not_enough_information: bool
    ai_model: str | None
    origin: Literal["ai", "user"]
    status: Literal["suggested", "accepted", "edited", "rejected"]
    decided_at: UtcDatetime | None
    version_id: int | None
    created_at: UtcDatetime


class ProposalAccept(BaseModel):
    # The lead's edited text; None accepts the AI's statement as it is
    statement: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)] | None = None


class UnseenCount(BaseModel):
    count: int


# ---------- approval ----------


class ApprovalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    version: int
    approved_by_name: str | None
    approved_at: UtcDatetime
    # Set when open High findings were approved over
    override_reason: str | None
    open_high_finding_ids: list[int]


class ApproveRequest(BaseModel):
    override_reason: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] | None = None
