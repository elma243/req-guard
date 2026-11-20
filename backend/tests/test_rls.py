"""Rule 12: every table a migration creates gets row level security in that same migration (PostgreSQL).

Each migration's upgrade() runs against a stand-in for Alembic's `op` that says it is on PostgreSQL and records the
tables it creates and the SQL it runs. No database is needed. Tables made before the RLS migration (a8c6d7e9f0b1)
were covered by that migration. test_postgresql.py checks the real flag (relrowsecurity) on Supabase.
"""

import re
from pathlib import Path
from unittest import mock

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

BACKEND_DIR = Path(__file__).resolve().parents[1]
RLS_MIGRATION = "a8c6d7e9f0b1"
_RLS = re.compile(r"ALTER TABLE (\w+) ENABLE ROW LEVEL SECURITY", re.IGNORECASE)


def _revisions_after_rls() -> list:
    script = ScriptDirectory.from_config(Config(str(BACKEND_DIR / "alembic.ini")))
    ordered = list(reversed(list(script.walk_revisions())))  # oldest first
    start = next(i for i, r in enumerate(ordered) if r.revision == RLS_MIGRATION)
    return ordered[start + 1 :]


def _run_upgrade(revision) -> tuple[set[str], set[str]]:
    created: set[str] = set()
    secured: set[str] = set()
    op = mock.MagicMock()
    op.get_bind.return_value.dialect.name = "postgresql"
    op.f.side_effect = lambda name: name
    op.create_table.side_effect = lambda name, *args, **kwargs: created.add(name)
    op.execute.side_effect = lambda sql, *args, **kwargs: secured.update(_RLS.findall(str(sql)))
    with mock.patch.object(revision.module, "op", op):
        revision.module.upgrade()
    return created, secured


@pytest.mark.parametrize("revision", _revisions_after_rls(), ids=lambda r: r.revision)
def test_every_new_table_enables_row_level_security(revision):
    created, secured = _run_upgrade(revision)
    assert created <= secured, f"{revision.revision}: no RLS for {sorted(created - secured)}"


def test_step_10_tables_are_checked():
    revision = next(r for r in _revisions_after_rls() if r.revision == "b2c3d4e5f6a7")
    created, secured = _run_upgrade(revision)
    assert created == {
        "repositories",
        "repo_snapshots",
        "repo_files",
        "repo_commits",
        "repo_pulls",
        "trace_target_embeddings",
        "trace_links",
    }
    assert created <= secured


def test_rls_is_skipped_on_sqlite():
    revision = next(r for r in _revisions_after_rls() if r.revision == "b2c3d4e5f6a7")
    op = mock.MagicMock()
    op.get_bind.return_value.dialect.name = "sqlite"
    with mock.patch.object(revision.module, "op", op):
        revision.module.upgrade()
    assert not any("ROW LEVEL SECURITY" in str(c) for c in op.execute.call_args_list)
