"""Schemas for the repository, its analysis and trace links (Step 10)."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

from app.schemas.common import UtcDatetime

LinkType = Literal["implements", "verifies"]
TargetType = Literal["file", "commit", "pr", "test_file", "test_function"]
LinkOrigin = Literal["explicit", "ai", "user"]
LinkStatus = Literal["suggested", "accepted", "rejected", "dismissed"]
Coverage = Literal["not_started", "code_only", "tests_only", "code_and_tests", "verified"]

_Loose = Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)]


# ---------- LLM answer ("verify_links") ----------


class LinkVerdict(BaseModel):
    pair_id: Annotated[str, StringConstraints(strip_whitespace=True, max_length=20)]
    verdict: Literal["link", "no_link", "not_enough_information"]
    confidence: float = Field(default=0.5, ge=0, le=1)
    reason: _Loose = ""


class LinkVerdicts(BaseModel):
    links: list[LinkVerdict] = Field(default_factory=list, max_length=100)


# ---------- repository ----------


class RepoConnect(BaseModel):
    repository: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=300)]


class RepoOut(BaseModel):
    full_name: str
    owner: str
    name: str
    url: str
    default_branch: str
    private: bool
    head_sha: str | None
    last_analyzed_at: UtcDatetime | None
    last_result: dict | None
    # Whether GITHUB_TOKEN is set; the token itself never leaves the backend
    token_configured: bool


class RepoState(BaseModel):
    repo: RepoOut | None
    token_configured: bool


class AnalysisResult(BaseModel):
    head_sha: str
    head_changed: bool
    new_commits: int
    commit_files_pending: int
    pulls_updated: int
    files: int
    python_files: int
    test_functions: int
    explicit_added: int
    suggested_added: int
    dismissed: int
    unchecked: int
    rate_limit_remaining: int | None
    github_requests: int
    notices: list[str]


class RecoverResult(BaseModel):
    explicit_added: int
    suggested_added: int
    dismissed: int
    unchecked: int
    notices: list[str]


class CommitOut(BaseModel):
    sha: str
    title: str
    author: str
    committed_at: UtcDatetime | None
    files: list[str]
    files_fetched: bool
    url: str


class PullOut(BaseModel):
    number: int
    title: str
    state: str
    merged: bool
    author: str
    head_ref: str
    url: str


class SnapshotFile(BaseModel):
    path: str
    kind: str
    tests: int
    url: str
    parse_error: str | None


class SnapshotOut(BaseModel):
    commit_sha: str | None
    file_count: int
    python_files: int
    test_functions: int
    truncated: bool
    python: list[SnapshotFile]
    commits: list[CommitOut]
    pulls: list[PullOut]


# ---------- trace links ----------


class TraceLinkOut(BaseModel):
    id: int
    link_type: LinkType
    target_type: TargetType
    target_key: str
    target_label: str
    target_detail: str | None
    url: str
    origin: LinkOrigin
    status: LinkStatus
    confidence: float | None
    similarity: float | None
    reason: str
    evidence: str | None
    ai_model: str | None
    llm_checked: bool
    # Counts toward coverage (accepted, or explicit and not rejected)
    live: bool
    # In the latest snapshot / stored commits and PRs; False after the file or test was removed
    present: bool
    # Step 8B test cases this test matches (TC-2), by name or by a TC-n mention
    test_cases: list[str]
    decided_by: str | None
    decided_at: UtcDatetime | None


class CriterionCoverage(BaseModel):
    code: str
    text: str
    covered: bool
    test_cases: list[str]


class TraceRequirement(BaseModel):
    id: int
    code: str
    statement: str
    module_name: str | None


class TraceRow(BaseModel):
    requirement: TraceRequirement
    coverage: Coverage
    coverage_reason: str
    criteria: list[CriterionCoverage]
    pending: int
    implements: list[TraceLinkOut]
    verifies: list[TraceLinkOut]


class TraceSummary(BaseModel):
    total: int
    counts: dict[str, int]
    pending: int


class TraceMatrix(BaseModel):
    repo: RepoOut | None
    summary: TraceSummary
    modules: list[str]
    rows: list[TraceRow]


class LinkCreate(BaseModel):
    requirement_id: int
    target_type: TargetType
    target_key: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


class TargetOut(BaseModel):
    target_type: TargetType
    link_type: LinkType
    key: str
    label: str
    detail: str | None
    url: str
