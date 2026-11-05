from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.schemas.common import UtcDatetime
from app.schemas.extraction import NfrCategory

RequirementType = Literal["functional", "non_functional"]
RequirementStatus = Literal["suggested", "accepted", "edited", "rejected", "merged"]


class ExtractRequest(BaseModel):
    # Read every message again (only "suggested" AI items are replaced)
    force: bool = False
    # Read only these messages (again)
    message_ids: list[int] | None = Field(default=None, max_length=1000)


class RequirementVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    version: int
    statement: str
    type: RequirementType
    nfr_category: NfrCategory | None
    actor: str
    confidence: float | None
    # provider:model that wrote this version, or "rule-based"; None for versions written by a user
    ai_model: str | None
    origin: Literal["ai", "user"]
    created_by_id: int | None
    # None for AI versions and for users who were deleted
    created_by_name: str | None
    created_at: UtcDatetime


class RequirementSourceOut(BaseModel):
    message_id: int
    # Exactly as in the client's message (may be Bangla or Banglish)
    source_sentence: str
    source: str
    channel: str
    author: str
    sent_at: UtcDatetime
    # The requirement this sentence came from through a merge; None for its own sentences
    merged_from_id: int | None


class RequirementSourceDetail(RequirementSourceOut):
    # The whole message, to show the sentence in its context
    message_text: str


class FailedCriterion(BaseModel):
    criterion: str
    points: int
    reason: str


class ValidationFindingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    requirement_id: int
    # The requirement version that was checked
    version: int
    category: Literal["vague", "missing_info", "out_of_scope", "module_alignment"]
    rule: str
    severity: Literal["low", "medium", "high"]
    reason: str
    evidence: str
    origin: Literal["ai", "user"]
    status: Literal["suggested", "accepted", "edited", "rejected"]
    created_at: UtcDatetime


class RequirementOut(BaseModel):
    id: int
    code: str
    origin: Literal["ai", "user"]
    status: RequirementStatus
    merged_into_id: int | None
    # Only while the status is "rejected"
    reject_reason: str | None
    created_at: UtcDatetime
    updated_at: UtcDatetime
    current: RequirementVersionOut
    version_count: int
    sources: list[RequirementSourceOut]
    # From the latest validation; quality_score is None until the first one
    quality_score: int | None
    quality_failed: list[FailedCriterion]
    module_name: str | None
    module_score: float | None
    open_findings: int
    has_issues: bool


class RequirementDetail(RequirementOut):
    sources: list[RequirementSourceDetail]
    versions: list[RequirementVersionOut]  # oldest first
    findings: list[ValidationFindingOut]


class RequirementUpdate(BaseModel):
    """Fields to change. A new version is created with the rest copied from the current one."""

    statement: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)] | None = None
    type: RequirementType | None = None
    nfr_category: NfrCategory | None = None
    actor: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] | None = None

    @model_validator(mode="after")
    def not_empty(self) -> "RequirementUpdate":
        if not self.model_fields_set:
            raise ValueError("Nothing to change")
        return self


class RejectRequest(BaseModel):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] | None = None


class MergeRequest(BaseModel):
    # Requirements folded into this one; their source sentences are added to it
    other_ids: list[int] = Field(min_length=1, max_length=100)


class BulkRequest(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=200)
    action: Literal["accept", "reject", "undo"]
    # Only for "reject"; the same reason for all of them
    reason: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] | None = None


class BulkSkipped(BaseModel):
    id: int
    code: str
    reason: str


class BulkResult(BaseModel):
    updated: list[RequirementOut]
    skipped: list[BulkSkipped]


class ValidateResult(BaseModel):
    validated: int
    # Open requirements with findings or failed criteria
    with_issues: int
    # Findings not rejected by a person
    findings: int
