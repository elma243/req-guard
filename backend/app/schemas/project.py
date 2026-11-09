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


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str
    scope_text: str
    modules: list[ProjectModule]
    auto_extract: bool = False
    voice_language: str = "auto"
    owner_id: int
    created_at: datetime
    updated_at: datetime
