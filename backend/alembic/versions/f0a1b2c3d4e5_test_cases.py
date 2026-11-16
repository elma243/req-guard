"""acceptance criteria and test cases

Revision ID: f0a1b2c3d4e5
Revises: e9f0a1b2c3d4
Create Date: 2026-11-15 14:00:00.000000

Step 8B (AR-05, FR-10): acceptance criteria and Given/When/Then test cases of approved requirements, each with
version rows (rule 6), plus per-requirement counters so AC / TC codes are never reused. Rule 12: every new table
gets row level security (PostgreSQL only).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f0a1b2c3d4e5"
down_revision: Union[str, Sequence[str], None] = "e9f0a1b2c3d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = (
    "acceptance_criteria",
    "acceptance_criterion_versions",
    "test_cases",
    "test_case_versions",
)

STATUSES = "status IN ('suggested', 'accepted', 'edited', 'rejected')"
ORIGINS = "origin IN ('ai', 'user')"


def _created_at() -> sa.Column:
    return sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def _fk(table: str, column: str, target: str, ondelete: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], [f"{target}.id"], name=op.f(f"fk_{table}_{column}_{target}"), ondelete=ondelete
    )


def _item_table(t: str) -> None:
    """acceptance_criteria and test_cases have the same columns."""
    op.create_table(
        t,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("requirement_id", sa.Integer(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("origin", sa.String(length=10), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("needs_review", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("requirement_version", sa.Integer(), nullable=False),
        sa.Column("ai_model", sa.String(length=100), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        _created_at(),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(ORIGINS, name=op.f(f"ck_{t}_origin")),
        sa.CheckConstraint(STATUSES, name=op.f(f"ck_{t}_status")),
        _fk(t, "requirement_id", "requirements", "CASCADE"),
        _fk(t, "created_by_id", "users", "SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{t}")),
        sa.UniqueConstraint("requirement_id", "number", name=op.f(f"uq_{t}_requirement_id_number")),
    )
    op.create_index(op.f(f"ix_{t}_requirement_id"), t, ["requirement_id"])


def upgrade() -> None:
    with op.batch_alter_table("requirements") as batch:
        batch.add_column(sa.Column("criterion_counter", sa.Integer(), server_default="0", nullable=False))
        batch.add_column(sa.Column("test_case_counter", sa.Integer(), server_default="0", nullable=False))

    _item_table("acceptance_criteria")

    t = "acceptance_criterion_versions"
    op.create_table(
        t,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("criterion_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("origin", sa.String(length=10), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        _created_at(),
        sa.CheckConstraint(ORIGINS, name=op.f(f"ck_{t}_origin")),
        _fk(t, "criterion_id", "acceptance_criteria", "CASCADE"),
        _fk(t, "created_by_id", "users", "SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{t}")),
        sa.UniqueConstraint("criterion_id", "version", name=op.f(f"uq_{t}_criterion_id_version")),
    )
    op.create_index(op.f(f"ix_{t}_criterion_id"), t, ["criterion_id"])

    _item_table("test_cases")

    t = "test_case_versions"
    op.create_table(
        t,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("test_case_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("type", sa.String(length=20), nullable=False),
        sa.Column("given", sa.Text(), nullable=False),
        sa.Column("when", sa.Text(), nullable=False),
        sa.Column("then", sa.Text(), nullable=False),
        sa.Column("criterion_id", sa.Integer(), nullable=True),
        sa.Column("origin", sa.String(length=10), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        _created_at(),
        sa.CheckConstraint(ORIGINS, name=op.f(f"ck_{t}_origin")),
        sa.CheckConstraint("type IN ('positive', 'negative', 'edge')", name=op.f(f"ck_{t}_type")),
        _fk(t, "test_case_id", "test_cases", "CASCADE"),
        _fk(t, "criterion_id", "acceptance_criteria", "SET NULL"),
        _fk(t, "created_by_id", "users", "SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{t}")),
        sa.UniqueConstraint("test_case_id", "version", name=op.f(f"uq_{t}_test_case_id_version")),
    )
    op.create_index(op.f(f"ix_{t}_test_case_id"), t, ["test_case_id"])
    op.create_index(op.f(f"ix_{t}_criterion_id"), t, ["criterion_id"])

    if op.get_bind().dialect.name == "postgresql":
        for table in TABLES:
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
    with op.batch_alter_table("requirements") as batch:
        batch.drop_column("test_case_counter")
        batch.drop_column("criterion_counter")
