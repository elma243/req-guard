"""json columns to jsonb

Revision ID: a5b6c7d8e9f0
Revises: f4a5b6c7d8e9
Create Date: 2026-11-07 18:00:00.000000

PostgreSQL's json type has no equality operator, so a query that compares whole rows (SELECT DISTINCT over a join
with requirements) failed with "could not identify an equality operator for type json". Every JSON column becomes
jsonb, which has one. PostgreSQL only: SQLite keeps its JSON (text) columns, and the models use JSON there.

A column default cannot be cast automatically while the type changes, so it is dropped and set again around it.
No new tables, so no ENABLE ROW LEVEL SECURITY here.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a5b6c7d8e9f0"
down_revision: Union[str, Sequence[str], None] = "f4a5b6c7d8e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (table, column, server default or None)
COLUMNS = (
    ("messages", "raw", None),
    ("projects", "modules", "'[]'"),
    ("requirements", "quality_failed", "'[]'"),
    ("sources", "config", None),
    ("team_members", "skills", None),
)


def _change_type(new_type: str) -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table, column, default in COLUMNS:
        if default is not None:
            op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} DROP DEFAULT")
        op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} TYPE {new_type} USING {column}::{new_type}")
        if default is not None:
            op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} SET DEFAULT {default}::{new_type}")


def upgrade() -> None:
    _change_type("jsonb")


def downgrade() -> None:
    _change_type("json")
