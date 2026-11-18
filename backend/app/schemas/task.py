"""Schemas for task groups, tasks, assignee suggestions and the Jira push (Step 9: FR-11, AR-06)."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

from app.schemas.common import UtcDatetime

TaskStatus = Literal["suggested", "assigned", "in_progress", "done"]
GroupKind = Literal["module", "cluster", "unmatched"]
Origin = Literal["ai", "user"]

MAX_GROUP_NAME_CHARS = 60
GroupName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_GROUP_NAME_CHARS)]
_Loose = Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)]


# ---------- LLM answer ("group_names") ----------


class GeneratedGroupName(BaseModel):
    ref: Annotated[str, StringConstraints(strip_whitespace=True, max_length=20)]
    name: _Loose = ""
    reason: _Loose = ""


class GeneratedGroupNames(BaseModel):
    not_enough_information: bool = False
    groups: list[GeneratedGroupName] = Field(default_factory=list, max_length=100)


# ---------- output ----------


class MemberRef(BaseModel):
    id: int
    name: str


class TaskRequirement(BaseModel):
    id: int
    code: str
    statement: str
    version: int
    approved: bool
    module_name: str | None


class TaskTestCases(BaseModel):
    total: int
    accepted: int
    codes: list[str]  # the accepted (or edited) ones


class SuggestionOut(BaseModel):
    member: MemberRef | None
    alternative: MemberRef | None
    reason: str
    score: float | None
    status: Literal["suggested", "accepted", "changed", "rejected"]


class TaskOut(BaseModel):
    id: int
    code: str
    group_id: int
    status: TaskStatus
    requirement: TaskRequirement
    test_cases: TaskTestCases
    grouping_reason: str
    group_origin: Origin
    assignee: MemberRef | None
    suggestion: SuggestionOut | None
    needs_review: bool
    needs_review_reason: str | None
    requirement_version: int
    jira_issue_key: str | None
    jira_url: str | None
    created_at: UtcDatetime
    updated_at: UtcDatetime


class TaskGroupOut(BaseModel):
    id: int
    name: str
    kind: GroupKind
    module_name: str | None
    reason: str
    origin: Origin
    status: Literal["suggested", "accepted", "edited"]
    ai_model: str | None
    tasks: list[TaskOut]


class WorkloadMember(BaseModel):
    id: int
    name: str
    role: str
    skills: list[str]


class MemberWorkload(BaseModel):
    member: WorkloadMember
    assigned: int
    in_progress: int
    done: int
    pending_suggestions: int


class JiraInfo(BaseModel):
    # Both the .env credentials and the project's Jira project for tasks are set
    configured: bool
    project_key: str | None
    issue_type: str
    # Names (never values) of the .env variables that are missing
    missing_env: list[str]


class TaskBoard(BaseModel):
    groups: list[TaskGroupOut]
    workload: list[MemberWorkload]
    modules: list[str]
    total_tasks: int
    jira: JiraInfo


class GroupItem(BaseModel):
    task_code: str
    requirement_code: str
    group_name: str
    reason: str


class GroupResult(BaseModel):
    created_tasks: int
    created_groups: int
    suggested: int
    items: list[GroupItem]
    notices: list[str]


class SuggestResult(BaseModel):
    suggested: int
    notices: list[str]


class AcceptAllResult(BaseModel):
    accepted: int
    skipped_needs_review: int


# ---------- input ----------


class AssignRequest(BaseModel):
    member_id: int


class TaskUpdate(BaseModel):
    status: TaskStatus | None = None
    group_id: int | None = None


class GroupRename(BaseModel):
    name: GroupName


class JiraPushRequest(BaseModel):
    task_ids: list[int] = Field(min_length=1, max_length=200)


class JiraPushed(BaseModel):
    task_id: int
    code: str
    key: str


class JiraSkipped(BaseModel):
    task_id: int
    code: str
    reason: str


class JiraPushResult(BaseModel):
    pushed: list[JiraPushed]
    skipped: list[JiraSkipped]
    error: str | None
