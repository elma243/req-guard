"""auto sync

Revision ID: c7d8e9f0a1b2
Revises: b6c7d8e9f0a1
Create Date: 2026-11-08 10:00:00.000000

Automatic sync of API sources: per-source settings (on/off, interval), the next run time, the lock
(`sync_started_at`), the count of failed runs in a row and the pause after errors. Per project: whether
auto-synced messages are extracted at once, and how many auto-synced messages were not looked at yet.
Telegram sources default to auto-sync on (Telegram keeps updates for about 24 hours only).
No new tables, so no row level security change (rule 12).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7d8e9f0a1b2"
down_revision: Union[str, Sequence[str], None] = "b6c7d8e9f0a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("sources") as batch:
        batch.add_column(sa.Column("auto_sync", sa.Boolean(), server_default=sa.false(), nullable=False))
        batch.add_column(sa.Column("sync_interval_minutes", sa.Integer(), server_default="5", nullable=False))
        batch.add_column(sa.Column("next_sync_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("sync_started_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("failure_count", sa.Integer(), server_default="0", nullable=False))
        batch.add_column(sa.Column("auto_sync_paused", sa.Boolean(), server_default=sa.false(), nullable=False))
        batch.create_check_constraint(
            op.f("ck_sources_sync_interval_minutes"), "sync_interval_minutes IN (1, 5, 15, 60)"
        )
    op.execute(sa.text("UPDATE sources SET auto_sync = :on WHERE type = 'telegram'").bindparams(on=True))

    with op.batch_alter_table("projects") as batch:
        batch.add_column(sa.Column("auto_extract", sa.Boolean(), server_default=sa.false(), nullable=False))
        batch.add_column(sa.Column("unseen_auto_messages", sa.Integer(), server_default="0", nullable=False))


def downgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.drop_column("unseen_auto_messages")
        batch.drop_column("auto_extract")

    with op.batch_alter_table("sources") as batch:
        batch.drop_constraint(op.f("ck_sources_sync_interval_minutes"), type_="check")
        batch.drop_column("auto_sync_paused")
        batch.drop_column("failure_count")
        batch.drop_column("sync_started_at")
        batch.drop_column("next_sync_at")
        batch.drop_column("sync_interval_minutes")
        batch.drop_column("auto_sync")
