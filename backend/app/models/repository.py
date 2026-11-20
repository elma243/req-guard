"""A project's GitHub repository and what one analysis read from it (Step 10).

One repository per project. An analysis (app/code/analyze.py) stores:
- `RepoSnapshot` + `RepoFile`: the file tree at one head commit, with the Python files parsed (functions, tests,
  REQ mentions). A snapshot is made once per head SHA, so analysing an unchanged repo downloads nothing again.
- `RepoCommit` and `RepoPull`: commits and pull requests, cached by SHA / number; only new ones are fetched.
- `TraceTargetEmbedding`: embeddings of target texts (file, test, commit, PR), cached by a hash of the text.

The GitHub token never lives here: it is read from GITHUB_TOKEN in backend/.env.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, JsonType
from app.models.embedding import EmbeddingVector

FILE_KINDS = ("code", "test", "other")


class Repository(Base):
    __tablename__ = "repositories"
    __table_args__ = (UniqueConstraint("project_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    owner: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(100))
    default_branch: Mapped[str] = mapped_column(String(255))
    private: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # Head commit of the default branch at the last analysis
    head_sha: Mapped[str | None] = mapped_column(String(40))
    last_analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Counts and notices of the last analysis (AnalysisResult without the repo)
    last_result: Mapped[dict | None] = mapped_column(JsonType)
    # GitHub's updated_at of the newest pull request read so far; older ones are not asked for again
    pulls_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Branches at the last analysis: [{"name", "sha"}] (branch names can carry a requirement ID)
    branches: Mapped[list[dict]] = mapped_column(JsonType, default=list, server_default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    snapshots: Mapped[list["RepoSnapshot"]] = relationship(
        back_populates="repository", cascade="all, delete-orphan", passive_deletes=True, order_by="RepoSnapshot.id"
    )

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"

    @property
    def html_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.name}"


class RepoSnapshot(Base):
    """The repository's files at one commit."""

    __tablename__ = "repo_snapshots"
    __table_args__ = (UniqueConstraint("repository_id", "commit_sha"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"), index=True)
    commit_sha: Mapped[str] = mapped_column(String(40))
    file_count: Mapped[int] = mapped_column(Integer, default=0)
    python_files: Mapped[int] = mapped_column(Integer, default=0)
    test_functions: Mapped[int] = mapped_column(Integer, default=0)
    # Some files were skipped (too large, vendored folders)
    truncated: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    repository: Mapped[Repository] = relationship(back_populates="snapshots")
    files: Mapped[list["RepoFile"]] = relationship(
        back_populates="snapshot", cascade="all, delete-orphan", passive_deletes=True, order_by="RepoFile.path"
    )


class RepoFile(Base):
    """One file of a snapshot. Only Python files have symbols and mentions."""

    __tablename__ = "repo_files"
    __table_args__ = (CheckConstraint(f"kind IN {FILE_KINDS}", name="kind"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("repo_snapshots.id", ondelete="CASCADE"), index=True)
    path: Mapped[str] = mapped_column(String(1000))
    kind: Mapped[str] = mapped_column(String(10))
    # [{kind function|class|test, name, qualname, line_start, line_end, docstring}]
    symbols: Mapped[list[dict]] = mapped_column(JsonType, default=list, server_default="[]")
    # REQ-number mentions: [{number, line, where comment|docstring|name, symbol (qualname or null)}]
    mentions: Mapped[list[dict]] = mapped_column(JsonType, default=list, server_default="[]")
    # First line of the module docstring (used for link suggestions)
    docstring: Mapped[str | None] = mapped_column(Text)
    parse_error: Mapped[str | None] = mapped_column(Text)

    snapshot: Mapped[RepoSnapshot] = relationship(back_populates="files")


class RepoCommit(Base):
    __tablename__ = "repo_commits"
    __table_args__ = (UniqueConstraint("repository_id", "sha"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"), index=True)
    sha: Mapped[str] = mapped_column(String(40))
    message: Mapped[str] = mapped_column(Text, default="")
    author: Mapped[str] = mapped_column(String(200), default="")
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Paths the commit changed
    files: Mapped[list[str]] = mapped_column(JsonType, default=list, server_default="[]")
    # False when the rate limit stopped the file list being read; the next analysis fills it in
    files_fetched: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())

    @property
    def title(self) -> str:
        return (self.message or "").strip().splitlines()[0] if (self.message or "").strip() else ""


class RepoPull(Base):
    __tablename__ = "repo_pulls"
    __table_args__ = (UniqueConstraint("repository_id", "number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    state: Mapped[str] = mapped_column(String(20), default="open")
    merged: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    merged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    head_ref: Mapped[str] = mapped_column(String(255), default="")
    author: Mapped[str] = mapped_column(String(200), default="")
    updated_at_github: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TraceTargetEmbedding(Base):
    """Embedding of one target text (file, test, commit or PR). Same text, same embedding, so it is cached."""

    __tablename__ = "trace_target_embeddings"
    __table_args__ = (UniqueConstraint("repository_id", "text_hash", "model"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"), index=True)
    # sha256 of the embedded text
    text_hash: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(100))
    embedding: Mapped[list[float]] = mapped_column(EmbeddingVector())
