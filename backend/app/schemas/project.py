import re
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

ProjectName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
LongText = Annotated[str, Field(max_length=20_000)]


class ProjectModule(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    description: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] = ""


def _unique_names(modules: list[ProjectModule]) -> list[ProjectModule]:
    names = [m.name.casefold() for m in modules]
    if len(set(names)) != len(names):
        raise ValueError("Module names must be different")
    return modules


ModuleList = Annotated[list[ProjectModule], Field(max_length=50), AfterValidator(_unique_names)]


def _jira_key(value: str) -> str:
    """A Jira project key like DEV or WEB2, upper case; "" clears the setting."""
    value = value.strip().upper()
    if value and not re.fullmatch(r"[A-Z][A-Z0-9_]{0,29}", value):
        raise ValueError("Jira project key looks like DEV or WEB2 (letters, digits, underscore)")
    return value


JiraKey = Annotated[str, AfterValidator(_jira_key)]
JiraIssueType = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]


class ProjectCreate(BaseModel):
    name: ProjectName
    description: LongText = ""
    scope_text: LongText = ""
    modules: ModuleList = []


class ProjectUpdate(BaseModel):
    """Only the fields that are sent are changed."""

    name: ProjectName | None = None
    description: LongText | None = None
    scope_text: LongText | None = None
    modules: ModuleList | None = None
    # Extract requirements from messages that auto-sync brings in, right after the sync
    auto_extract: bool | None = None
    # Language of voice notes for transcription: auto-detect, Bangla or English
    voice_language: Literal["auto", "bn", "en"] | None = None
    # Where "Push to Jira" creates issues for tasks (Step 9); "" clears it
    jira_task_project_key: JiraKey | None = None
    jira_task_issue_type: JiraIssueType | None = None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str
    scope_text: str
    modules: list[ProjectModule]
    auto_extract: bool = False
    voice_language: str = "auto"
    jira_task_project_key: str | None = None
    jira_task_issue_type: str = "Task"
    owner_id: int
    created_at: datetime
    updated_at: datetime
