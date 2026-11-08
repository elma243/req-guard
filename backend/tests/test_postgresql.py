"""The requirement flow on real PostgreSQL. Skipped unless TEST_DATABASE_URL points to a PostgreSQL database
migrated to head (alembic upgrade head):

    TEST_DATABASE_URL=postgresql+psycopg://... pytest tests/test_postgresql.py

SQLite misses PostgreSQL-only errors, such as comparing json values (SELECT DISTINCT over requirements failed with
"could not identify an equality operator for type json"). Everything runs inside one transaction that is rolled
back at the end; the app's own commits only release savepoints, so nothing is left in the database.
"""

import os
import uuid
from contextlib import nullcontext
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from app.api.deps import get_optional_llm_client
from app.core.db import get_db
from app.main import app
from app.connectors import sync
from app.connectors.scheduler import AutoSyncScheduler
from app.models import Project, Requirement, RequirementEmbedding, Source, User

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL not set (PostgreSQL check)"
)

# Rule-based extraction keeps each sentence; the first two are near-duplicates for the pair checks
CHAT = (
    "20/10/26, 10:05 - Karim: Order list lagbe.\n"
    "20/10/26, 10:06 - Karim: Order list lagbe please.\n"
    "21/10/26, 09:00 - Karim: Report taratari lagbe.\n"
)
JSON_COLUMNS = {
    ("messages", "raw"),
    ("projects", "modules"),
    ("requirements", "quality_failed"),
    ("sources", "config"),
    ("team_members", "skills"),
}


@pytest.fixture
def pg():
    engine = create_engine(os.environ["TEST_DATABASE_URL"])
    connection = engine.connect()
    assert connection.dialect.name == "postgresql"
    outer = connection.begin()
    db = Session(bind=connection, join_transaction_mode="create_savepoint", autoflush=False, expire_on_commit=False)

    def use_pg():
        yield db

    app.dependency_overrides[get_db] = use_pg
    try:
        yield db
    finally:
        db.close()
        outer.rollback()
        connection.close()
        engine.dispose()


def test_json_columns_are_jsonb(pg):
    rows = pg.execute(
        text(
            "SELECT table_name, column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND data_type IN ('json', 'jsonb')"
        )
    ).all()
    types = {(table, column): data_type for table, column, data_type in rows}
    assert {key: types.get(key) for key in JSON_COLUMNS} == {key: "jsonb" for key in JSON_COLUMNS}


def test_extract_validate_and_extract_again(pg, client, outbox):
    email = f"pg-check-{uuid.uuid4().hex[:12]}@example.com"
    body = {"name": "PG check", "email": email, "password": "password123", "confirm_password": "password123"}
    assert client.post("/auth/register", json=body).status_code == 201
    code = outbox.code_for(email)
    assert client.post("/auth/verify-email", json={"email": email, "code": code}).status_code == 200
    token = client.post("/auth/login", json={"email": email, "password": "password123"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    modules = [{"name": "Orders", "description": "order list report"}]
    project = client.post("/projects", json={"name": "PG check"}, headers=headers).json()
    base = f"/projects/{project['id']}"
    assert client.patch(base, json={"modules": modules, "scope_text": "orders and reports"}, headers=headers).status_code == 200

    app.dependency_overrides[get_optional_llm_client] = lambda: None
    files = [("files", ("WhatsApp Chat with Karim.txt", CHAT.encode(), "text/plain"))]
    assert client.post(f"{base}/uploads", files=files, headers=headers).status_code == 200

    first = client.post(f"{base}/requirements/extract", headers=headers)
    assert first.status_code == 200, first.text
    assert first.json()["requirements_created"] == 3

    validated = client.post(f"{base}/requirements/validate", headers=headers)
    assert validated.status_code == 200, validated.text
    assert validated.json()["similarity_unavailable_reason"] is None
    listed = client.get(f"{base}/requirements", headers=headers).json()
    assert all(r["quality_score"] is not None for r in listed)
    assert any(r["quality_failed"] for r in listed)  # the jsonb column holds data, as in the bug
    findings = client.get(f"{base}/requirements/findings", headers=headers).json()
    assert any(f["category"] == "duplicate" for f in findings)
    # One pgvector embedding per requirement version
    assert pg.scalar(select(func.count()).select_from(RequirementEmbedding)) >= 3

    # Extracting the same messages again deletes the untouched suggestions first (_delete_replaceable)
    again = client.post(f"{base}/requirements/extract", json={"force": True}, headers=headers)
    assert again.status_code == 200, again.text
    assert again.json()["requirements_created"] == 3
    codes = pg.scalars(
        select(Requirement.code).where(Requirement.project_id == project["id"]).order_by(Requirement.number)
    ).all()
    assert codes == ["REQ-004", "REQ-005", "REQ-006"]
    assert client.post(f"{base}/requirements/validate", headers=headers).status_code == 200


class _SharedSession:
    """Session factory for the scheduler that hands out the test's session and leaves closing to the fixture."""

    def __init__(self, db: Session):
        self.db = db

    def __call__(self):
        return nullcontext(self.db)


def test_auto_sync_tick_on_postgresql(pg, monkeypatch):
    """The lock UPDATE, the scheduler, the badge count and auto-extract on real PostgreSQL."""
    from tests.test_auto_sync import T0, FakeClock, FakeConnector, Script, msg

    user = User(name="PG", email=f"pg-sync-{uuid.uuid4().hex[:12]}@example.com", password_hash="x")
    pg.add(user)
    pg.flush()
    project = Project(name="PG auto-sync", owner_id=user.id, auto_extract=True)
    pg.add(project)
    pg.flush()
    source = Source(
        project_id=project.id,
        type="slack",
        display_name="Acme Slack",
        config={"origin": "api", "settings": {}, "state": {}},
        auto_sync=True,
        next_sync_at=T0,
    )
    pg.add(source)
    pg.commit()

    # The lock: a second taker waits, a stale lock is taken over
    assert sync.acquire_lock(pg, source.id, T0)
    assert not sync.acquire_lock(pg, source.id, T0 + timedelta(minutes=1))
    assert sync.acquire_lock(pg, source.id, T0 + sync.LOCK_TIMEOUT + timedelta(seconds=1))
    pg.execute(text("UPDATE sources SET sync_started_at = NULL WHERE id = :id"), {"id": source.id})
    pg.commit()

    script = Script().then([msg("pg-1", "The dashboard must show the order list.")])
    monkeypatch.setattr(sync, "build_connector", lambda s: FakeConnector(script))
    scheduler = AutoSyncScheduler(_SharedSession(pg), FakeClock(), llm_factory=lambda: None)
    assert scheduler.tick() == 1

    pg.expire_all()
    source = pg.get(Source, source.id)
    assert source.sync_status == "ok" and source.sync_started_at is None
    assert source.next_sync_at == T0 + timedelta(minutes=5)
    assert pg.get(Project, project.id).unseen_auto_messages == 1
    assert pg.scalar(select(func.count()).select_from(Requirement).where(Requirement.project_id == project.id)) == 1
