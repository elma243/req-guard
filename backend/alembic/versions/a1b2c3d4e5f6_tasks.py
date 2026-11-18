"""task groups and tasks

Revision ID: a1b2c3d4e5f6
Revises: f0a1b2c3d4e5
Create Date: 2026-11-17 12:00:00.000000

Step 9 (FR-11, AR-06): approved requirements grouped into task groups, one task per requirement with an AI-suggested
assignee the lead confirms, plus the project's task counter and the Jira project tasks are pushed to. Rule 12: every
new table gets row level security (PostgreSQL only).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "f0a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = ("task_groups", "tasks")

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
ORIGINS = "('ai', 'user')"


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def _fk(table: str, column: str, target: str, ondelete: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], [f"{target}.id"], name=op.f(f"fk_{table}_{column}_{target}"), ondelete=ondelete
    )


def upgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.add_column(sa.Column("task_counter", sa.Integer(), server_default="0", nullable=False))
        batch.add_column(sa.Column("jira_task_project_key", sa.String(length=30), nullable=True))
        batch.add_column(sa.Column("jira_task_issue_type", sa.String(length=50), server_default="Task", nullable=False))

    t = "task_groups"
    op.create_table(
        t,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("module_name", sa.String(length=100), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("origin", sa.String(length=10), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("ai_model", sa.String(length=100), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("kind IN ('module', 'cluster', 'unmatched')", name=op.f(f"ck_{t}_kind")),
        sa.CheckConstraint(f"origin IN {ORIGINS}", name=op.f(f"ck_{t}_origin")),
        sa.CheckConstraint("status IN ('suggested', 'accepted', 'edited')", name=op.f(f"ck_{t}_status")),
        _fk(t, "project_id", "projects", "CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{t}")),
    )
    op.create_index(op.f(f"ix_{t}_project_id"), t, ["project_id"])

    t = "tasks"
    op.create_table(
        t,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("requirement_id", sa.Integer(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("grouping_reason", sa.Text(), nullable=False),
        sa.Column("group_origin", sa.String(length=10), nullable=False),
        sa.Column("assignee_id", sa.Integer(), nullable=True),
        sa.Column("suggested_assignee_id", sa.Integer(), nullable=True),
        sa.Column("alternative_assignee_id", sa.Integer(), nullable=True),
        sa.Column("suggestion_reason", sa.Text(), nullable=True),
        sa.Column("suggestion_score", sa.Float(), nullable=True),
        sa.Column("suggestion_status", sa.String(length=20), nullable=True),
        sa.Column("rejected_member_ids", JSON, server_default="[]", nullable=False),
        sa.Column("needs_review", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("needs_review_reason", sa.Text(), nullable=True),
        sa.Column("requirement_version", sa.Integer(), nullable=False),
        sa.Column("jira_issue_key", sa.String(length=50), nullable=True),
        sa.Column("jira_pushed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "status IN ('suggested', 'assigned', 'in_progress', 'done')", name=op.f(f"ck_{t}_status")
        ),
        sa.CheckConstraint(f"group_origin IN {ORIGINS}", name=op.f(f"ck_{t}_group_origin")),
        sa.CheckConstraint(
            "suggestion_status IS NULL OR suggestion_status IN ('suggested', 'accepted', 'changed', 'rejected')",
            name=op.f(f"ck_{t}_suggestion_status"),
        ),
        _fk(t, "project_id", "projects", "CASCADE"),
        _fk(t, "group_id", "task_groups", "CASCADE"),
        _fk(t, "requirement_id", "requirements", "CASCADE"),
        sa.ForeignKeyConstraint(
            ["assignee_id"], ["team_members.id"], name=op.f(f"fk_{t}_assignee_id_team_members"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["suggested_assignee_id"],
            ["team_members.id"],
            name=op.f(f"fk_{t}_suggested_assignee_id_team_members"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["alternative_assignee_id"],
            ["team_members.id"],
            name=op.f(f"fk_{t}_alternative_assignee_id_team_members"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{t}")),
        sa.UniqueConstraint("project_id", "number", name=op.f(f"uq_{t}_project_id_number")),
        sa.UniqueConstraint("requirement_id", name=op.f(f"uq_{t}_requirement_id")),
    )
    op.create_index(op.f(f"ix_{t}_project_id"), t, ["project_id"])
    op.create_index(op.f(f"ix_{t}_group_id"), t, ["group_id"])
    op.create_index(op.f(f"ix_{t}_assignee_id"), t, ["assignee_id"])

    if op.get_bind().dialect.name == "postgresql":
        for table in TABLES:
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
    with op.batch_alter_table("projects") as batch:
        batch.drop_column("jira_task_issue_type")
        batch.drop_column("jira_task_project_key")
        batch.drop_column("task_counter")
