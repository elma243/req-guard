"""requirement validation

Revision ID: d2b3c4e5f6a7
Revises: c1a2b3d4e5f6
Create Date: 2026-11-05 12:30:00.000000

Findings of the validation rules, and on each requirement its latest quality score, failed criteria and
best-matching module. Rule 12: the new table gets row level security (PostgreSQL only; SQLite has no RLS).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d2b3c4e5f6a7"
down_revision: Union[str, Sequence[str], None] = "c1a2b3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "validation_findings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("requirement_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(length=20), nullable=False),
        sa.Column("rule", sa.String(length=40), nullable=False),
        sa.Column("severity", sa.String(length=10), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("origin", sa.String(length=10), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "category IN ('vague', 'missing_info', 'out_of_scope', 'module_alignment')",
            name=op.f("ck_validation_findings_category"),
        ),
        sa.CheckConstraint("severity IN ('low', 'medium', 'high')", name=op.f("ck_validation_findings_severity")),
        sa.CheckConstraint("origin IN ('ai', 'user')", name=op.f("ck_validation_findings_origin")),
        sa.CheckConstraint(
            "status IN ('suggested', 'accepted', 'edited', 'rejected')", name=op.f("ck_validation_findings_status")
        ),
        sa.ForeignKeyConstraint(
            ["requirement_id"],
            ["requirements.id"],
            name=op.f("fk_validation_findings_requirement_id_requirements"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_validation_findings")),
    )
    op.create_index(op.f("ix_validation_findings_requirement_id"), "validation_findings", ["requirement_id"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE validation_findings ENABLE ROW LEVEL SECURITY")

    with op.batch_alter_table("requirements") as batch:
        batch.add_column(sa.Column("quality_score", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("quality_failed", sa.JSON(), server_default="[]", nullable=False))
        batch.add_column(sa.Column("module_name", sa.String(length=100), nullable=True))
        batch.add_column(sa.Column("module_score", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("requirements") as batch:
        batch.drop_column("module_score")
        batch.drop_column("module_name")
        batch.drop_column("quality_failed")
        batch.drop_column("quality_score")
    op.drop_index(op.f("ix_validation_findings_requirement_id"), table_name="validation_findings")
    op.drop_table("validation_findings")
