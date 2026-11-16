"""Schemas for acceptance criteria and Given/When/Then test cases (Step 8B: AR-05, FR-10)."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.schemas.common import UtcDatetime

# Limits the app keeps; the AI's longer texts are dropped (a cut sentence would read badly)
MAX_CRITERION_CHARS = 500
MAX_TITLE_CHARS = 200
MAX_STEP_CHARS = 1000

ItemStatus = Literal["suggested", "accepted", "edited", "rejected"]
TestCaseType = Literal["positive", "negative", "edge"]
Origin = Literal["ai", "user"]

CriterionText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_CRITERION_CHARS)]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_TITLE_CHARS)]
Step = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_STEP_CHARS)]
# Generous in the LLM answer, so one long item does not fail the whole answer; the code drops what is too long
_Loose = Annotated[str, StringConstraints(strip_whitespace=True, max_length=3000)]


# ---------- LLM answer ----------


class GeneratedCriterion(BaseModel):
    ref: Annotated[str, StringConstraints(strip_whitespace=True, max_length=20)]
    text: _Loose


class GeneratedTestCase(BaseModel):
    title: _Loose
    type: TestCaseType
    given: _Loose
    when: _Loose
    then: _Loose
    criterion_ref: Annotated[str, StringConstraints(strip_whitespace=True, max_length=20)] = ""


class GeneratedTestCases(BaseModel):
    not_enough_information: bool = False
    reason: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] = ""
    criteria: list[GeneratedCriterion] = Field(default_factory=list, max_length=20)
    test_cases: list[GeneratedTestCase] = Field(default_factory=list, max_length=20)


# ---------- output ----------


class CriterionVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    version: int
    text: str
    origin: Origin
    created_by_name: str | None
    created_at: UtcDatetime


class CriterionOut(BaseModel):
    id: int
    requirement_id: int
    code: str
    text: str
    origin: Origin
    status: ItemStatus
    # The requirement changed after it was written (a new version); a person should look at it again
    needs_review: bool
    requirement_version: int
    ai_model: str | None
    # Test cases (not rejected) that verify it
    test_case_count: int
    versions: list[CriterionVersionOut]
    created_at: UtcDatetime
    updated_at: UtcDatetime


class TestCaseVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    __test__ = False

    version: int
    title: str
    type: TestCaseType
    given: str
    when: str
    then: str
    criterion_id: int | None
    origin: Origin
    created_by_name: str | None
    created_at: UtcDatetime


class TestCaseOut(BaseModel):
    __test__ = False

    id: int
    requirement_id: int
    code: str
    title: str
    type: TestCaseType
    given: str
    when: str
    then: str
    # None when written without one, or when a regeneration removed the criterion
    criterion_id: int | None
    criterion_code: str | None
    criterion_status: ItemStatus | None
    origin: Origin
    status: ItemStatus
    needs_review: bool
    requirement_version: int
    ai_model: str | None
    versions: list[TestCaseVersionOut]
    created_at: UtcDatetime
    updated_at: UtcDatetime


class RequirementSummary(BaseModel):
    id: int
    code: str
    statement: str
    version: int
    status: str
    approved: bool


class TestCaseGroup(BaseModel):
    __test__ = False

    requirement: RequirementSummary
    criteria: list[CriterionOut]
    test_cases: list[TestCaseOut]


class GenerateTestCasesItem(BaseModel):
    requirement_id: int
    code: str
    created_test_cases: int
    created_criteria: int
    # The AI said it could not write test cases from what it was given (nothing was stored)
    not_enough_information: bool
    reason: str = ""


class GenerateTestCasesResult(BaseModel):
    created_test_cases: int
    created_criteria: int
    items: list[GenerateTestCasesItem]
    # Codes of requirements not reached before the time limit: run again for them
    skipped: list[str] = Field(default_factory=list)
    # Set when a later call failed: the earlier results were kept
    error: str | None = None


# ---------- input ----------


class CriterionIn(BaseModel):
    text: CriterionText


class TestCaseIn(BaseModel):
    __test__ = False

    title: Title
    type: TestCaseType
    given: Step
    when: Step
    then: Step
    criterion_id: int | None = None
