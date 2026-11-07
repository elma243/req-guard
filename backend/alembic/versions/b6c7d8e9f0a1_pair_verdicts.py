"""pair verdicts

Revision ID: b6c7d8e9f0a1
Revises: a5b6c7d8e9f0
Create Date: 2026-11-07 21:00:00.000000

The LLM checks every pair the similarity models flag: its answers are kept per pair of requirement versions
(requirement_pair_verdicts), and a pair finding records whether it was confirmed and the LLM's reason.
Rule 12: the new table gets row level security (PostgreSQL only).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b6c7d8e9f0a1"
down_revision: Union[str, Sequence[str], None] = "a5b6c7d8e9f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

RELATIONS = (
    "relation IN ('same', 'duplicate', 'conflict', 'change_request', 'related_but_different', 'unrelated', "
    "'not_enough_information')"
)


def upgrade() -> None:
    op.create_table(
        "requirement_pair_verdicts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("older_version_id", sa.Integer(), nullable=False),
        sa.Column("newer_version_id", sa.Integer(), nullable=False),
        sa.Column("relation", sa.String(length=30), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("ai_model", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(RELATIONS, name=op.f("ck_requirement_pair_verdicts_relation")),
        sa.ForeignKeyConstraint(
            ["older_version_id"],
            ["requirement_versions.id"],
            name=op.f("fk_requirement_pair_verdicts_older_version_id_requirement_versions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["newer_version_id"],
            ["requirement_versions.id"],
            name=op.f("fk_requirement_pair_verdicts_newer_version_id_requirement_versions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_requirement_pair_verdicts")),
        sa.UniqueConstraint(
            "older_version_id",
            "newer_version_id",
            name=op.f("uq_requirement_pair_verdicts_older_version_id_newer_version_id"),
        ),
    )
    op.create_index(
        op.f("ix_requirement_pair_verdicts_older_version_id"), "requirement_pair_verdicts", ["older_version_id"]
    )
    op.create_index(
        op.f("ix_requirement_pair_verdicts_newer_version_id"), "requirement_pair_verdicts", ["newer_version_id"]
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE requirement_pair_verdicts ENABLE ROW LEVEL SECURITY")

    with op.batch_alter_table("validation_findings") as batch:
        batch.add_column(sa.Column("confirmation", sa.String(length=20), nullable=True))
        batch.add_column(sa.Column("explanation", sa.Text(), nullable=True))
        batch.create_check_constraint(
            op.f("ck_validation_findings_confirmation"),
            "confirmation IS NULL OR confirmation IN ('confirmed', 'unconfirmed')",
        )


def downgrade() -> None:
    with op.batch_alter_table("validation_findings") as batch:
        batch.drop_constraint(op.f("ck_validation_findings_confirmation"), type_="check")
        batch.drop_column("explanation")
        batch.drop_column("confirmation")
    op.drop_index(op.f("ix_requirement_pair_verdicts_newer_version_id"), table_name="requirement_pair_verdicts")
    op.drop_index(op.f("ix_requirement_pair_verdicts_older_version_id"), table_name="requirement_pair_verdicts")
    op.drop_table("requirement_pair_verdicts")
