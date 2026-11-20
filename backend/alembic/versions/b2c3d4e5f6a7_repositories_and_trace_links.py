"""repositories, snapshots, commits, pull requests and trace links

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-11-18 12:00:00.000000

Step 10: one GitHub repository per project, what an analysis read from it (snapshots of the file tree with parsed
Python files, commits, pull requests, cached target embeddings) and trace links from approved requirements to code
and tests. Rule 12: every new table gets row level security (PostgreSQL only).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = (
    "repositories",
    "repo_snapshots",
    "repo_files",
    "repo_commits",
    "repo_pulls",
    "trace_target_embeddings",
    "trace_links",
)

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def _fk(table: str, column: str, target: str, ondelete: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], [f"{target}.id"], name=op.f(f"fk_{table}_{column}_{target}"), ondelete=ondelete
    )


def _repo_child(table: str) -> list:
    """id + repository_id with its foreign key, primary key and index arguments."""
    return [
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("repository_id", sa.Integer(), nullable=False),
        _fk(table, "repository_id", "repositories", "CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{table}")),
    ]


def _embedding_type() -> sa.types.TypeEngine:
    """pgvector's vector(384) on PostgreSQL, JSON on SQLite (as in f4a5b6c7d8e9)."""
    if op.get_bind().dialect.name == "postgresql":
        from pgvector.sqlalchemy import Vector

        return Vector(384)
    return sa.JSON()


def upgrade() -> None:
    t = "repositories"
    op.create_table(
        t,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("owner", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("default_branch", sa.String(length=255), nullable=False),
        sa.Column("private", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("head_sha", sa.String(length=40), nullable=True),
        sa.Column("last_analyzed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_result", JSON, nullable=True),
        sa.Column("pulls_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("branches", JSON, server_default="[]", nullable=False),
        *_timestamps(),
        _fk(t, "project_id", "projects", "CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{t}")),
        sa.UniqueConstraint("project_id", name=op.f(f"uq_{t}_project_id")),
    )

    t = "repo_snapshots"
    op.create_table(
        t,
        *_repo_child(t),
        sa.Column("commit_sha", sa.String(length=40), nullable=False),
        sa.Column("file_count", sa.Integer(), nullable=False),
        sa.Column("python_files", sa.Integer(), nullable=False),
        sa.Column("test_functions", sa.Integer(), nullable=False),
        sa.Column("truncated", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("repository_id", "commit_sha", name=op.f(f"uq_{t}_repository_id_commit_sha")),
    )
    op.create_index(op.f(f"ix_{t}_repository_id"), t, ["repository_id"])

    t = "repo_files"
    op.create_table(
        t,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column("path", sa.String(length=1000), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("symbols", JSON, server_default="[]", nullable=False),
        sa.Column("mentions", JSON, server_default="[]", nullable=False),
        sa.Column("docstring", sa.Text(), nullable=True),
        sa.Column("parse_error", sa.Text(), nullable=True),
        sa.CheckConstraint("kind IN ('code', 'test', 'other')", name=op.f(f"ck_{t}_kind")),
        _fk(t, "snapshot_id", "repo_snapshots", "CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{t}")),
    )
    op.create_index(op.f(f"ix_{t}_snapshot_id"), t, ["snapshot_id"])

    t = "repo_commits"
    op.create_table(
        t,
        *_repo_child(t),
        sa.Column("sha", sa.String(length=40), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("author", sa.String(length=200), nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("files", JSON, server_default="[]", nullable=False),
        sa.Column("files_fetched", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.UniqueConstraint("repository_id", "sha", name=op.f(f"uq_{t}_repository_id_sha")),
    )
    op.create_index(op.f(f"ix_{t}_repository_id"), t, ["repository_id"])

    t = "repo_pulls"
    op.create_table(
        t,
        *_repo_child(t),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("merged", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("merged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("head_ref", sa.String(length=255), nullable=False),
        sa.Column("author", sa.String(length=200), nullable=False),
        sa.Column("updated_at_github", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("repository_id", "number", name=op.f(f"uq_{t}_repository_id_number")),
    )
    op.create_index(op.f(f"ix_{t}_repository_id"), t, ["repository_id"])

    t = "trace_target_embeddings"
    op.create_table(
        t,
        *_repo_child(t),
        sa.Column("text_hash", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("embedding", _embedding_type(), nullable=False),
        sa.UniqueConstraint(
            "repository_id", "text_hash", "model", name=op.f(f"uq_{t}_repository_id_text_hash_model")
        ),
    )
    op.create_index(op.f(f"ix_{t}_repository_id"), t, ["repository_id"])

    t = "trace_links"
    op.create_table(
        t,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("repository_id", sa.Integer(), nullable=False),
        sa.Column("requirement_id", sa.Integer(), nullable=False),
        sa.Column("link_type", sa.String(length=20), nullable=False),
        sa.Column("target_type", sa.String(length=20), nullable=False),
        sa.Column("target_key", sa.String(length=1000), nullable=False),
        sa.Column("target_label", sa.Text(), nullable=False),
        sa.Column("target_detail", sa.Text(), nullable=True),
        sa.Column("target_line", sa.Integer(), nullable=True),
        sa.Column("origin", sa.String(length=10), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("similarity", sa.Float(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column("ai_model", sa.String(length=100), nullable=True),
        sa.Column("llm_checked", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("requirement_version", sa.Integer(), nullable=False),
        sa.Column("decided_by_id", sa.Integer(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("link_type IN ('implements', 'verifies')", name=op.f(f"ck_{t}_link_type")),
        sa.CheckConstraint(
            "target_type IN ('file', 'commit', 'pr', 'test_file', 'test_function')", name=op.f(f"ck_{t}_target_type")
        ),
        sa.CheckConstraint("origin IN ('explicit', 'ai', 'user')", name=op.f(f"ck_{t}_origin")),
        sa.CheckConstraint(
            "status IN ('suggested', 'accepted', 'rejected', 'dismissed')", name=op.f(f"ck_{t}_status")
        ),
        _fk(t, "project_id", "projects", "CASCADE"),
        _fk(t, "repository_id", "repositories", "CASCADE"),
        _fk(t, "requirement_id", "requirements", "CASCADE"),
        _fk(t, "decided_by_id", "users", "SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{t}")),
        sa.UniqueConstraint(
            "requirement_id", "target_type", "target_key", name=op.f(f"uq_{t}_requirement_id_target_type_target_key")
        ),
    )
    op.create_index(op.f(f"ix_{t}_project_id"), t, ["project_id"])
    op.create_index(op.f(f"ix_{t}_repository_id"), t, ["repository_id"])
    op.create_index(op.f(f"ix_{t}_requirement_id"), t, ["requirement_id"])

    if op.get_bind().dialect.name == "postgresql":
        for table in TABLES:
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
