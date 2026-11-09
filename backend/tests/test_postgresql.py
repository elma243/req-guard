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


def test_voice_audio_has_row_level_security(pg):
    enabled = pg.scalar(text("SELECT relrowsecurity FROM pg_class WHERE relname = 'voice_audio'"))
    assert enabled is True


def test_voice_note_transcription_on_postgresql(pg, monkeypatch):
    """Storing a voice note with its audio (bytea), the claim UPDATE, transcription and auto-extract on PostgreSQL."""
    import httpx
    import respx

    from app.ai import speech
    from app.ai.speech import GROQ_URL, GroqWhisper, TranscriptionWorker
    from app.connectors.base import AudioAttachment, voice_message
    from app.connectors.importer import import_messages
    from app.models import Message, VoiceAudio
    from tests.test_auto_sync import T0

    monkeypatch.setattr(speech, "sleep", lambda seconds: None)
    user = User(name="PG", email=f"pg-voice-{uuid.uuid4().hex[:12]}@example.com", password_hash="x")
    pg.add(user)
    pg.flush()
    project = Project(name="PG voice", owner_id=user.id, auto_extract=True, voice_language="bn")
    pg.add(project)
    pg.commit()

    attachment = AudioAttachment(file_id="v1", filename="v1.opus", mime_type="audio/ogg", data=b"\x00fake-opus")
    note = voice_message(
        source="whatsapp", parent_id="pg-voice", channel="Acme", author="Karim", sent_at=T0,
        attachment=attachment, raw={},
    )
    counts = import_messages(pg, project.id, None, [note])
    pg.commit()
    message_id = counts.new_ids[0]
    assert pg.get(VoiceAudio, message_id).data == b"\x00fake-opus"

    # The claim: a second claimer finds nothing while it is being transcribed
    claimed = speech.claim_next(pg, T0)
    assert claimed.id == message_id
    assert speech.claim_next(pg, T0 + timedelta(minutes=1)) is None
    pg.execute(text("UPDATE messages SET transcript_status = 'pending' WHERE id = :id"), {"id": message_id})
    pg.commit()

    with respx.mock:
        respx.post(GROQ_URL).mock(
            return_value=httpx.Response(200, json={"text": "The dashboard must show the order list.",
                                                   "language": "bengali", "duration": 3.0})
        )
        worker = TranscriptionWorker(
            _SharedSession(pg), lambda: T0,
            transcriber_factory=lambda: GroqWhisper("gsk-test", ["whisper-large-v3"]),
            llm_factory=lambda: None,
        )
        assert worker.tick() == 1

    pg.expire_all()
    message = pg.get(Message, message_id)
    assert (message.transcript_status, message.raw["voice"]["language"]) == ("done", "bangla")
    assert message.raw["voice"]["detected_language"] == "bengali"
    assert pg.get(VoiceAudio, message_id) is None
    assert pg.scalar(select(func.count()).select_from(Requirement).where(Requirement.project_id == project.id)) == 1
