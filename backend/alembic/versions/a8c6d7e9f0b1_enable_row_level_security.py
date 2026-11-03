"""enable row level security

Revision ID: a8c6d7e9f0b1
Revises: f7a5b6c8d9e0
Create Date: 2026-11-03 10:00:00.000000

CLAUDE.md rule 12: every table has row level security enabled, so Supabase's public API (anon key) cannot
read or change it. No policies are added: the backend connects as the table owner, which RLS does not
restrict. PostgreSQL only; SQLite has no RLS.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a8c6d7e9f0b1"
down_revision: Union[str, Sequence[str], None] = "f7a5b6c8d9e0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = (
    "users",
    "projects",
    "team_members",
    "sources",
    "messages",
    "requirements",
    "requirement_versions",
    "requirement_sources",
    "alembic_version",
)


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table in TABLES:
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table in TABLES:
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
