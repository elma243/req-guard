"""requirement version ai model

Revision ID: f7a5b6c8d9e0
Revises: e6f4a5b7c8d9
Create Date: 2026-10-28 10:00:00.000000

The LLM model that wrote each AI requirement version (main model or the fallback).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f7a5b6c8d9e0"
down_revision: Union[str, Sequence[str], None] = "e6f4a5b7c8d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("requirement_versions") as batch:
        batch.add_column(sa.Column("ai_model", sa.String(length=100), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("requirement_versions") as batch:
        batch.drop_column("ai_model")
