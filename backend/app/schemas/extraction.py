"""LLM answer schema for the "extract_requirements" prompt, and the result of an extraction run."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, model_validator

NfrCategory = Literal["performance", "security", "usability", "reliability", "other"]


class ExtractedRequirement(BaseModel):
    statement: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)] = Field(
        description='The requirement in English, written as "The system shall ..."'
    )
    type: Literal["functional", "non_functional"]
    nfr_category: NfrCategory | None = Field(default=None, description="Only for non_functional requirements")
    actor: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] = Field(
        default="", description='Who needs it, e.g. "customer", "admin"; empty if not stated'
    )
    sentence_ids: list[str] = Field(min_length=1, description='Ids of the source sentences, e.g. ["m12.s1"]')
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def category_matches_type(self) -> "ExtractedRequirement":
        if self.type == "functional":
            self.nfr_category = None
        elif self.nfr_category is None:
            self.nfr_category = "other"
        return self


class ExtractionAnswer(BaseModel):
    requirements: list[ExtractedRequirement]


class ExtractResult(BaseModel):
    messages_processed: int = 0
    # Messages with only greetings / small talk: marked processed without asking the LLM
    messages_small_talk: int = 0
    # Messages still waiting (over the per-run limit or the run's time budget)
    messages_remaining: int = 0
    requirements_created: int = 0
    llm_calls: int = 0
    # Batches done with the rule-based fallback because no AI model answered (or none is configured)
    rule_based_batches: int = 0
    # "AI unavailable — used rule-based extraction." when any batch used the fallback
    notice: str | None = None
    # Why the AI was not used for those batches (no key, no message text)
    ai_error: str | None = None
