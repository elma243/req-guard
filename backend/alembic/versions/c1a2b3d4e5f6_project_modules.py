"""project modules

Revision ID: c1a2b3d4e5f6
Revises: b9d7e8f0a1c2
Create Date: 2026-11-05 12:00:00.000000

The product's parts as a JSON list of {name, description}; validation matches requirements against them.
A column only, no new table, so no ENABLE ROW LEVEL SECURITY here.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c1a2b3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "b9d7e8f0a1c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.add_column(sa.Column("modules", sa.JSON(), server_default="[]", nullable=False))


def downgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.drop_column("modules")
