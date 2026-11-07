"""The Alembic migrations run forwards and backwards on SQLite."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.core.config import settings

BACKEND_DIR = Path(__file__).resolve().parents[1]


def test_upgrade_and_downgrade(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'migrate.db').as_posix()}"
    monkeypatch.setattr(settings, "database_url", url)  # alembic/env.py reads this
    config = Config(str(BACKEND_DIR / "alembic.ini"))

    command.upgrade(config, "head")
    engine = create_engine(url)
    assert {"users", "projects", "team_members", "sources", "messages"} <= set(inspect(engine).get_table_names())
    checks = inspect(engine).get_check_constraints("team_members")
    assert any(c["name"] == "ck_team_members_role" for c in checks)
    source_columns = {c["name"] for c in inspect(engine).get_columns("sources")}
    assert {"credentials_encrypted", "sync_status", "sync_error"} <= source_columns
    assert {"requirements", "requirement_versions", "requirement_sources"} <= set(inspect(engine).get_table_names())
    assert "requirements_extracted_at" in {c["name"] for c in inspect(engine).get_columns("messages")}
    assert {"requirement_counter", "modules"} <= {c["name"] for c in inspect(engine).get_columns("projects")}
    requirement_checks = {c["name"] for c in inspect(engine).get_check_constraints("requirements")}
    assert {"ck_requirements_origin", "ck_requirements_status"} <= requirement_checks
    assert {"reject_reason", "previous_status"} <= {c["name"] for c in inspect(engine).get_columns("requirements")}
    assert "merged_from_id" in {c["name"] for c in inspect(engine).get_columns("requirement_sources")}
    assert "validation_findings" in inspect(engine).get_table_names()
    assert {c["name"] for c in inspect(engine).get_check_constraints("validation_findings")} == {
        "ck_validation_findings_category",
        "ck_validation_findings_severity",
        "ck_validation_findings_origin",
        "ck_validation_findings_status",
        "ck_validation_findings_confirmation",
    }
    assert {"confirmation", "explanation"} <= {c["name"] for c in inspect(engine).get_columns("validation_findings")}
    assert "requirement_pair_verdicts" in inspect(engine).get_table_names()
    assert {"quality_score", "quality_failed", "module_name", "module_score"} <= {
        c["name"] for c in inspect(engine).get_columns("requirements")
    }

    assert {"email_otps", "password_reset_tokens"} <= set(inspect(engine).get_table_names())
    assert {"email_verified_at", "failed_logins", "locked_until", "token_version"} <= {
        c["name"] for c in inspect(engine).get_columns("users")
    }

    assert "requirement_embeddings" in inspect(engine).get_table_names()
    assert {"related_requirement_id", "score"} <= {c["name"] for c in inspect(engine).get_columns("validation_findings")}
    with engine.begin() as connection:  # the pair categories pass the recreated check constraint
        category_check = connection.execute(
            text("SELECT sql FROM sqlite_master WHERE name = 'validation_findings'")
        ).scalar_one()
    assert "change_request" in category_check

    # Deleting a project removes its sources and messages in the database itself
    def on_delete(table: str, referred: str) -> str | None:
        fks = inspect(engine).get_foreign_keys(table)
        return next(fk["options"].get("ondelete") for fk in fks if fk["referred_table"] == referred)

    assert on_delete("sources", "projects") == "CASCADE"
    assert on_delete("messages", "projects") == "CASCADE"
    assert on_delete("team_members", "projects") == "CASCADE"
    assert on_delete("requirements", "projects") == "CASCADE"
    assert on_delete("requirement_versions", "requirements") == "CASCADE"
    assert on_delete("requirement_sources", "messages") == "CASCADE"
    assert on_delete("validation_findings", "requirements") == "CASCADE"
    assert on_delete("email_otps", "users") == "CASCADE"
    assert on_delete("requirement_embeddings", "requirement_versions") == "CASCADE"
    assert on_delete("requirement_pair_verdicts", "requirement_versions") == "CASCADE"
    related = [
        fk
        for fk in inspect(engine).get_foreign_keys("validation_findings")
        if fk["constrained_columns"] == ["related_requirement_id"]
    ]
    assert [(fk["referred_table"], fk["options"].get("ondelete")) for fk in related] == [("requirements", "CASCADE")]
    assert on_delete("password_reset_tokens", "users") == "CASCADE"
    merged_from = [
        fk
        for fk in inspect(engine).get_foreign_keys("requirement_sources")
        if fk["constrained_columns"] == ["merged_from_id"]
    ]
    assert [(fk["referred_table"], fk["options"].get("ondelete")) for fk in merged_from] == [("requirements", "SET NULL")]

    command.downgrade(config, "base")
    assert set(inspect(engine).get_table_names()) <= {"alembic_version"}
    engine.dispose()


def test_accounts_from_before_the_auth_migration_stay_able_to_log_in(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'migrate.db').as_posix()}"
    monkeypatch.setattr(settings, "database_url", url)
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    command.upgrade(config, "d2b3c4e5f6a7")
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO users (name, email, password_hash) VALUES ('Old', 'old@example.com', 'x')")
        )

    command.upgrade(config, "head")

    with engine.connect() as connection:
        row = connection.execute(text("SELECT email_verified_at, failed_logins, token_version FROM users")).one()
    assert row.email_verified_at is not None
    assert (row.failed_logins, row.token_version) == (0, 0)
    engine.dispose()
