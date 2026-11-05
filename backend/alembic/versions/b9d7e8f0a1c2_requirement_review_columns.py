"""requirement review columns

Revision ID: b9d7e8f0a1c2
Revises: a8c6d7e9f0b1
Create Date: 2026-11-05 10:00:00.000000

What the review screen needs: an optional reject reason, the status undo returns to, and which requirement
a merged source row was copied from (so restoring that requirement can remove the copy).
Columns only, no new tables, so no ENABLE ROW LEVEL SECURITY here.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b9d7e8f0a1c2"
down_revision: Union[str, Sequence[str], None] = "a8c6d7e9f0b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("requirements") as batch:
        batch.add_column(sa.Column("reject_reason", sa.Text(), nullable=True))
        batch.add_column(sa.Column("previous_status", sa.String(length=20), nullable=True))
    with op.batch_alter_table("requirement_sources") as batch:
        batch.add_column(sa.Column("merged_from_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            op.f("fk_requirement_sources_merged_from_id_requirements"),
            "requirements",
            ["merged_from_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("requirement_sources") as batch:
        batch.drop_constraint(op.f("fk_requirement_sources_merged_from_id_requirements"), type_="foreignkey")
        batch.drop_column("merged_from_id")
    with op.batch_alter_table("requirements") as batch:
        batch.drop_column("previous_status")
        batch.drop_column("reject_reason")
