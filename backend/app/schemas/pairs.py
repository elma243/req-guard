"""The LLM's answer about pairs of requirements (prompt app/ai/prompts/classify_pairs.md)."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

PairRelation = Literal[
    "same",
    "duplicate",
    "conflict",
    "change_request",
    "related_but_different",
    "unrelated",
    "not_enough_information",
]


class PairJudgement(BaseModel):
    pair_id: str
    relation: PairRelation
    # One sentence, in English
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class PairJudgements(BaseModel):
    pairs: list[PairJudgement] = Field(max_length=100)
