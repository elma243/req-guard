"""clarifications and approvals

Revision ID: e9f0a1b2c3d4
Revises: d8e9f0a1b2c3
Create Date: 2026-11-15 10:00:00.000000

Step 8A (AR-04, FR-09, FR-22): clarification questions linked to the finding they resolve, single-use share links
for the client (only a keyed hash of the token is stored), the client's answers, the AI's proposed updates from the
answers, and requirement approvals (a record, separate from "accepted"). Rule 12: every new table gets row level
security (PostgreSQL only).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "e9f0a1b2c3d4"
down_revision: Union[str, Sequence[str], None] = "d8e9f0a1b2c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = (
    "clarification_questions",
    "clarification_links",
    "clarification_link_questions",
    "clarification_answers",
    "clarification_proposals",
    "requirement_approvals",
)

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _created_at() -> sa.Column:
    return sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def _fk(table: str, column: str, target: str, ondelete: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], [f"{target}.id"], name=op.f(f"fk_{table}_{column}_{target}"), ondelete=ondelete
    )


def upgrade() -> None:
    t = "clarification_questions"
    op.create_table(
        t,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("requirement_id", sa.Integer(), nullable=False),
        sa.Column("finding_id", sa.Integer(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("origin", sa.String(length=10), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("ai_model", sa.String(length=100), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        _created_at(),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("origin IN ('ai', 'user')", name=op.f("ck_clarification_questions_origin")),
        sa.CheckConstraint(
            "status IN ('suggested', 'edited', 'accepted', 'rejected')", name=op.f("ck_clarification_questions_status")
        ),
        _fk(t, "requirement_id", "requirements", "CASCADE"),
        _fk(t, "finding_id", "validation_findings", "SET NULL"),
        _fk(t, "created_by_id", "users", "SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_clarification_questions")),
    )
    op.create_index(op.f("ix_clarification_questions_requirement_id"), t, ["requirement_id"])
    op.create_index(op.f("ix_clarification_questions_finding_id"), t, ["finding_id"])

    t = "clarification_links"
    op.create_table(
        t,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        _created_at(),
        _fk(t, "project_id", "projects", "CASCADE"),
        _fk(t, "created_by_id", "users", "SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_clarification_links")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_clarification_links_token_hash")),
    )
    op.create_index(op.f("ix_clarification_links_project_id"), t, ["project_id"])

    t = "clarification_link_questions"
    op.create_table(
        t,
        sa.Column("link_id", sa.Integer(), nullable=False),
        sa.Column("question_id", sa.Integer(), nullable=False),
        _fk(t, "link_id", "clarification_links", "CASCADE"),
        _fk(t, "question_id", "clarification_questions", "CASCADE"),
        sa.PrimaryKeyConstraint("link_id", "question_id", name=op.f("pk_clarification_link_questions")),
    )

    t = "clarification_answers"
    op.create_table(
        t,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("question_id", sa.Integer(), nullable=False),
        sa.Column("requirement_id", sa.Integer(), nullable=False),
        sa.Column("link_id", sa.Integer(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("answered_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=True),
        _fk(t, "question_id", "clarification_questions", "CASCADE"),
        _fk(t, "requirement_id", "requirements", "CASCADE"),
        _fk(t, "link_id", "clarification_links", "SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_clarification_answers")),
    )
    op.create_index(op.f("ix_clarification_answers_question_id"), t, ["question_id"])
    op.create_index(op.f("ix_clarification_answers_requirement_id"), t, ["requirement_id"])

    t = "clarification_proposals"
    op.create_table(
        t,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("requirement_id", sa.Integer(), nullable=False),
        sa.Column("based_on_version", sa.Integer(), nullable=False),
        sa.Column("answer_ids", JSON, server_default="[]", nullable=False),
        sa.Column("statement", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("not_enough_information", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("ai_model", sa.String(length=100), nullable=True),
        sa.Column("origin", sa.String(length=10), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("decided_by_id", sa.Integer(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version_id", sa.Integer(), nullable=True),
        _created_at(),
        sa.CheckConstraint("origin IN ('ai', 'user')", name=op.f("ck_clarification_proposals_origin")),
        sa.CheckConstraint(
            "status IN ('suggested', 'accepted', 'edited', 'rejected')", name=op.f("ck_clarification_proposals_status")
        ),
        _fk(t, "requirement_id", "requirements", "CASCADE"),
        _fk(t, "decided_by_id", "users", "SET NULL"),
        _fk(t, "version_id", "requirement_versions", "SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_clarification_proposals")),
    )
    op.create_index(op.f("ix_clarification_proposals_requirement_id"), t, ["requirement_id"])

    t = "requirement_approvals"
    op.create_table(
        t,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("requirement_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("approved_by_id", sa.Integer(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("override_reason", sa.Text(), nullable=True),
        sa.Column("open_high_finding_ids", JSON, server_default="[]", nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(length=100), nullable=True),
        _fk(t, "requirement_id", "requirements", "CASCADE"),
        _fk(t, "approved_by_id", "users", "SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_requirement_approvals")),
    )
    op.create_index(op.f("ix_requirement_approvals_requirement_id"), t, ["requirement_id"])

    if op.get_bind().dialect.name == "postgresql":
        for table in TABLES:
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
