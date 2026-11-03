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
    created_at: UtcDatetime


class RequirementSourceOut(BaseModel):
    message_id: int
    # Exactly as in the client's message (may be Bangla or Banglish)
    source_sentence: str
    source: str
    channel: str
    author: str
    sent_at: UtcDatetime


class RequirementOut(BaseModel):
    id: int
    code: str
    origin: Literal["ai", "user"]
    status: RequirementStatus
    merged_into_id: int | None
    created_at: UtcDatetime
    updated_at: UtcDatetime
    current: RequirementVersionOut
    version_count: int
    sources: list[RequirementSourceOut]


class RequirementDetail(RequirementOut):
    versions: list[RequirementVersionOut]  # oldest first


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


class MergeRequest(BaseModel):
    # Requirements folded into this one; their source sentences are added to it
    other_ids: list[int] = Field(min_length=1, max_length=100)
