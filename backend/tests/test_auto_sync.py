"""Automatic sync of API sources: the per-source lock, the scheduler (with a fake clock), the pause after errors,
the Gmail reconnect state, auto-extract and the new-messages count.

No external API is called: `build_connector` is replaced by a fake connector that returns given messages or
raises a given error.
"""

from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.main as main_module
from app.api import sources as sources_api
from app.connectors import scheduler as scheduler_module
from app.connectors import sync
from app.connectors.scheduler import AutoSyncScheduler, spread_offset
from app.connectors.base import NormalizedMessage
from app.connectors.http import ConnectorError, NeedsAuthError
from app.connectors.importer import import_messages
from app.core.config import settings
from app.core.security import create_oauth_state
from app.models import Message, Project, Requirement, Source, User

T0 = datetime(2026, 11, 8, 10, 0, tzinfo=timezone.utc)


# ---------- fakes and helpers ----------


class FakeConnector:
    """Stands in for an API connector: each fetch() returns the next queued result or raises it."""

    def __init__(self, script: "Script"):
        self.script = script
        self.state = {"cursor": script.calls}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def fetch(self, since=None):
        self.script.calls += 1
        self.script.since.append(since)
        result = self.script.results.pop(0) if self.script.results else []
        if isinstance(result, Exception):
            raise result
        return result


class FakeClock:
    def __init__(self, start: datetime = T0):
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta) -> datetime:
        self.now += timedelta(**delta)
        return self.now


class Script:
    def __init__(self):
        self.results: list = []
        self.calls = 0
        self.since: list = []

    def then(self, *results):
        self.results.extend(results)
        return self


@pytest.fixture
def script(monkeypatch):
    s = Script()
    monkeypatch.setattr(sync, "build_connector", lambda source: FakeConnector(s))
    return s


def msg(external_id: str, text: str = "The dashboard must show the order list.", source: str = "slack"):
    return NormalizedMessage(
        source=source,
        external_id=external_id,
        channel="client-acme",
        author="Karim",
        sent_at=T0 - timedelta(hours=1),
        text=text,
    )


@pytest.fixture
def db(db_session_factory):
    session = db_session_factory()
    yield session
    session.close()


@pytest.fixture
def project(db):
    user = User(name="Owner", email="owner@example.com", password_hash="x", email_verified_at=T0)
    db.add(user)
    db.flush()
    project = Project(name="Acme", owner_id=user.id)
    db.add(project)
    db.commit()
    return project


def add_source(db, project, type_="slack", name=None, **fields) -> Source:
    source = Source(
        project_id=project.id,
        type=type_,
        display_name=name or f"{type_} source",
        config={"origin": "api", "settings": {}, "state": {}},
        **fields,
    )
    db.add(source)
    db.commit()
    return source


# ---------- the lock ----------


def test_sync_imports_and_releases_the_lock(db, project, script):
    source = add_source(db, project)
    script.then([msg("1"), msg("2")])
    outcome = sync.run_sync(db, source, T0)
    assert outcome.status == "ok" and outcome.imported == 2 and len(outcome.new_message_ids) == 2
    db.refresh(source)
    assert source.sync_started_at is None
    assert source.sync_status == "ok"


def test_second_sync_waits_for_the_lock(db, project, script):
    source = add_source(db, project)
    assert sync.acquire_lock(db, source.id, T0)
    assert not sync.acquire_lock(db, source.id, T0 + timedelta(minutes=1))
    with pytest.raises(sync.SyncLocked):
        sync.run_sync(db, source, T0 + timedelta(minutes=1))
    assert script.calls == 0


def test_stale_lock_is_taken_over(db, project, script):
    source = add_source(db, project)
    assert sync.acquire_lock(db, source.id, T0)
    later = T0 + sync.LOCK_TIMEOUT + timedelta(seconds=1)
    assert sync.run_sync(db, source, later).status == "ok"
    assert script.calls == 1


def test_lock_released_after_a_failed_sync(db, project, script):
    source = add_source(db, project)
    script.then(ConnectorError("Slack is down"))
    outcome = sync.run_sync(db, source, T0)
    assert outcome.status == "error" and outcome.error == "Slack is down"
    db.refresh(source)
    assert source.sync_started_at is None and source.sync_error == "Slack is down"
    assert sync.acquire_lock(db, source.id, T0)


def test_lock_released_when_storing_fails(db, project, script, monkeypatch):
    source = add_source(db, project)

    def broken_import(*args, **kwargs):
        raise RuntimeError("database went away")

    monkeypatch.setattr(sync, "import_messages", broken_import)
    with pytest.raises(RuntimeError):
        sync.run_sync(db, source, T0)
    db.refresh(source)
    assert source.sync_started_at is None


def test_manual_sync_answers_409_while_locked(client, make_user, db_session_factory, script, monkeypatch):
    monkeypatch.setattr(settings, "slack_bot_token", "xoxb-test")
    _, headers = make_user()
    project_id = client.post("/projects", json={"name": "Acme"}, headers=headers).json()["id"]
    source = client.post(
        f"/projects/{project_id}/sources",
        json={"type": "slack", "display_name": "Acme Slack", "settings": {"channels": ["client-acme"]}},
        headers=headers,
    ).json()

    with db_session_factory() as other:
        assert sync.acquire_lock(other, source["id"], sync.utcnow())
    res = client.post(f"/projects/{project_id}/sources/{source['id']}/sync", headers=headers)
    assert res.status_code == 409
    assert res.json()["detail"].startswith("A sync for this source is already running")
    assert script.calls == 0


# ---------- the scheduler ----------


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def scheduler(db_session_factory, clock):
    # No LLM: backend/.env may hold a real key; auto-extract then uses the rule-based fallback
    return AutoSyncScheduler(db_session_factory, clock, llm_factory=lambda: None)


def reload(db, source: Source) -> Source:
    db.expire_all()
    return db.get(Source, source.id)


def test_new_sources_are_spread_over_their_interval(db, project, script, scheduler, clock):
    a = add_source(db, project, name="a", auto_sync=True)
    b = add_source(db, project, name="b", auto_sync=True)
    assert scheduler.tick() == 0  # first tick only schedules
    a, b = reload(db, a), reload(db, b)
    assert sync.as_utc(a.next_sync_at) == T0 + spread_offset(a.id, 5)
    assert sync.as_utc(b.next_sync_at) == T0 + spread_offset(b.id, 5)
    assert a.next_sync_at != b.next_sync_at

    clock.now = T0 + spread_offset(a.id, 5)
    assert scheduler.tick() == 1  # only a is due
    clock.now = T0 + spread_offset(b.id, 5)
    assert scheduler.tick() == 1  # then b
    assert script.calls == 2


def test_runs_again_after_the_interval(db, project, script, scheduler, clock):
    source = add_source(db, project, auto_sync=True, sync_interval_minutes=15, next_sync_at=T0)
    assert scheduler.tick() == 1
    assert sync.as_utc(reload(db, source).next_sync_at) == T0 + timedelta(minutes=15)

    clock.advance(minutes=14, seconds=59)
    assert scheduler.tick() == 0
    clock.advance(seconds=1)
    assert scheduler.tick() == 1
    assert script.calls == 2


def test_only_auto_synced_api_sources_run(db, project, script, scheduler):
    add_source(db, project, name="off", auto_sync=False, next_sync_at=T0)
    add_source(db, project, type_="whatsapp", name="upload", auto_sync=True, next_sync_at=T0)
    assert scheduler.tick() == 0
    assert script.calls == 0


def test_run_is_skipped_while_the_previous_sync_is_still_going(db, project, script, scheduler, clock):
    source = add_source(db, project, auto_sync=True, next_sync_at=T0)
    assert sync.acquire_lock(db, source.id, T0 - timedelta(minutes=1))  # e.g. a manual sync
    assert scheduler.tick() == 0
    assert script.calls == 0
    assert sync.as_utc(reload(db, source).next_sync_at) == T0 + timedelta(minutes=5)


def test_at_most_max_per_tick_sources_per_tick(db, project, script, db_session_factory, clock):
    for n in range(4):
        add_source(db, project, name=f"s{n}", auto_sync=True, next_sync_at=T0 - timedelta(minutes=n))
    scheduler = AutoSyncScheduler(db_session_factory, clock, max_per_tick=3, llm_factory=lambda: None)
    assert scheduler.tick() == 3
    assert scheduler.tick() == 1


def test_new_auto_synced_messages_are_counted_for_the_badge(db, project, script, scheduler):
    add_source(db, project, auto_sync=True, next_sync_at=T0)
    script.then([msg("1"), msg("2")])
    scheduler.tick()
    db.expire_all()
    assert db.get(Project, project.id).unseen_auto_messages == 2


def test_unexpected_error_in_a_run_does_not_stop_the_tick(db, project, script, scheduler, monkeypatch):
    add_source(db, project, name="a", auto_sync=True, next_sync_at=T0 - timedelta(minutes=1))
    add_source(db, project, name="b", auto_sync=True, next_sync_at=T0)
    real = sync.run_sync
    calls = []

    def flaky(db, source, now=None):
        calls.append(source.display_name)
        if source.display_name == "a":
            raise RuntimeError("boom")
        return real(db, source, now)

    monkeypatch.setattr(sync, "run_sync", flaky)
    assert scheduler.tick() == 2
    assert calls == ["a", "b"]


# ---------- the app lifespan ----------


def test_scheduler_off_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "auto_sync_enabled", False)
    started = []
    monkeypatch.setattr(main_module, "start_scheduler", lambda: started.append(1))
    with TestClient(main_module.app):
        assert main_module.app.state.auto_sync_task is None
    assert started == []


def test_scheduler_started_and_stopped_with_the_app(monkeypatch):
    monkeypatch.setattr(settings, "auto_sync_enabled", True)
    ticks = []
    monkeypatch.setattr(AutoSyncScheduler, "tick", lambda self: ticks.append(1) or 0)
    with TestClient(main_module.app):
        task = main_module.app.state.auto_sync_task
        assert task is not None and not task.done()
    assert task.cancelled() or task.done()


# ---------- pause after errors ----------


def test_three_failures_in_a_row_pause_auto_sync(db, project, script, scheduler, clock):
    source = add_source(db, project, auto_sync=True, sync_interval_minutes=1, next_sync_at=T0)
    script.then(ConnectorError("down"), ConnectorError("down"), ConnectorError("still down"))
    for _ in range(3):
        assert scheduler.tick() == 1
        clock.advance(minutes=1)
    source = reload(db, source)
    assert source.failure_count == 3 and source.auto_sync_paused
    assert source.sync_error == "still down"

    clock.advance(hours=1)
    assert scheduler.tick() == 0  # paused sources are left alone
    assert script.calls == 3


def test_a_success_resets_the_failure_count(db, project, script, scheduler, clock):
    source = add_source(db, project, auto_sync=True, sync_interval_minutes=1, next_sync_at=T0)
    script.then(ConnectorError("down"), ConnectorError("down"), [msg("1")], ConnectorError("down"))
    for _ in range(4):
        scheduler.tick()
        clock.advance(minutes=1)
    source = reload(db, source)
    assert source.failure_count == 1 and not source.auto_sync_paused


def test_retry_with_a_successful_sync_ends_the_pause(db, project, script, scheduler, clock):
    source = add_source(db, project, auto_sync=True, failure_count=3, auto_sync_paused=True, sync_status="error")
    script.then([msg("1")])
    assert sync.run_sync(db, source, T0).status == "ok"  # what the Retry button does
    source = reload(db, source)
    assert source.failure_count == 0 and not source.auto_sync_paused and source.sync_error is None


def test_failures_without_auto_sync_do_not_pause(db, project, script):
    source = add_source(db, project, auto_sync=False)
    script.then(*(ConnectorError("down") for _ in range(3)))
    for _ in range(3):
        sync.run_sync(db, source, T0)
    source = reload(db, source)
    assert source.failure_count == 3 and not source.auto_sync_paused


# ---------- Gmail: expired refresh token ----------


def gmail_source(db, project, **fields):
    return add_source(
        db, project, type_="gmail", auto_sync=True, next_sync_at=T0, credentials_encrypted="encrypted-token", **fields
    )


def test_expired_google_token_stops_auto_sync(db, project, script, scheduler, clock):
    source = gmail_source(db, project)
    script.then(NeedsAuthError("Google access expired or was revoked. Connect the Google account again."))
    assert scheduler.tick() == 1
    source = reload(db, source)
    assert source.sync_status == "needs_auth"
    assert source.credentials_encrypted is None
    assert source.failure_count == 0 and not source.auto_sync_paused  # not an "error" pause
    assert source.auto_sync  # the setting is kept for when the account is connected again

    clock.advance(hours=2)
    assert scheduler.tick() == 0
    assert script.calls == 1


def test_gmail_without_an_account_is_not_auto_synced(db, project, script, scheduler):
    add_source(db, project, type_="gmail", auto_sync=True, next_sync_at=T0)
    assert scheduler.tick() == 0


def test_reconnecting_google_starts_auto_sync_again(client, db, project, script, scheduler, clock, monkeypatch):
    source = gmail_source(db, project, sync_status="needs_auth", failure_count=2)
    source.credentials_encrypted = None
    db.commit()
    monkeypatch.setattr(settings, "token_encryption_key", Fernet.generate_key().decode())
    monkeypatch.setattr(sources_api, "exchange_code", lambda code, **kwargs: "1//new-refresh-token")

    state = create_oauth_state(project.owner_id, source.id)
    res = client.get("/oauth/gmail/callback", params={"state": state, "code": "ok"}, follow_redirects=False)
    assert res.status_code == 303 and res.headers["location"].endswith("gmail=connected")

    source = reload(db, source)
    assert source.sync_status is None and source.failure_count == 0 and source.credentials_encrypted
    assert source.next_sync_at is not None
    clock.now = sync.as_utc(source.next_sync_at)
    assert scheduler.tick() == 1


# ---------- auto-extract ----------


def requirement_message_ids(db, project) -> set[int]:
    return {
        s.message_id
        for r in db.scalars(select(Requirement).where(Requirement.project_id == project.id))
        for s in r.sources
    }


def test_auto_extract_reads_only_the_new_messages(db, project, script, scheduler):
    project.auto_extract = True
    source = add_source(db, project, auto_sync=True, next_sync_at=T0)
    # Imported earlier (e.g. an upload) and not extracted yet: auto-extract leaves it for the Extract button
    import_messages(db, project.id, None, [msg("old", "The report must be exported as PDF.", source="whatsapp")])
    db.commit()
    old = db.scalar(select(Message).where(Message.external_id == "old"))

    script.then([msg("new", "The dashboard must show the order list.")])
    assert scheduler.tick() == 1

    db.expire_all()
    new = db.scalar(select(Message).where(Message.external_id == "new"))
    assert new.source_id == source.id and new.requirements_extracted_at is not None
    assert db.get(Message, old.id).requirements_extracted_at is None
    assert requirement_message_ids(db, project) == {new.id}
    requirement = db.scalar(select(Requirement).where(Requirement.project_id == project.id))
    assert requirement.origin == "ai" and requirement.status == "suggested"
    assert requirement.quality_score is not None  # validated too


def test_no_extraction_when_auto_extract_is_off(db, project, script, scheduler):
    add_source(db, project, auto_sync=True, next_sync_at=T0)
    script.then([msg("new")])
    assert scheduler.tick() == 1
    db.expire_all()
    assert db.scalar(select(Message)).requirements_extracted_at is None
    assert requirement_message_ids(db, project) == set()


def test_failed_auto_extract_keeps_the_sync(db, project, script, scheduler, monkeypatch):
    project.auto_extract = True
    source = add_source(db, project, auto_sync=True, next_sync_at=T0)
    db.commit()

    def broken(*args, **kwargs):
        raise RuntimeError("spaCy model missing")

    monkeypatch.setattr(scheduler_module, "extract_requirements", broken)
    script.then([msg("new")])
    assert scheduler.tick() == 1
    source = reload(db, source)
    assert source.sync_status == "ok" and source.failure_count == 0
    assert db.scalar(select(Message)).requirements_extracted_at is None


# ---------- API ----------


@pytest.fixture
def api_owner(client, make_user, monkeypatch):
    monkeypatch.setattr(settings, "slack_bot_token", "xoxb-test")
    monkeypatch.setattr(settings, "telegram_bot_token", "123:test")
    _, headers = make_user()
    project_id = client.post("/projects", json={"name": "Acme"}, headers=headers).json()["id"]
    return headers, project_id


def create_api_source(client, headers, project_id, type_="slack"):
    settings_by_type = {"slack": {"channels": ["client-acme"]}, "telegram": {}}
    res = client.post(
        f"/projects/{project_id}/sources",
        json={"type": type_, "display_name": f"Acme {type_}", "settings": settings_by_type[type_]},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return res.json()


def test_telegram_auto_syncs_by_default_others_do_not(client, api_owner):
    headers, project_id = api_owner
    telegram = create_api_source(client, headers, project_id, "telegram")
    slack = create_api_source(client, headers, project_id, "slack")
    assert telegram["auto_sync"] is True and telegram["sync_interval_minutes"] == 5
    assert slack["auto_sync"] is False
    assert slack["auto_sync_paused"] is False and slack["syncing"] is False
    assert slack["auto_sync_available"] is False  # tests run with AUTO_SYNC_ENABLED=false


def test_turn_auto_sync_on_and_change_the_interval(client, api_owner):
    headers, project_id = api_owner
    source = create_api_source(client, headers, project_id)
    url = f"/projects/{project_id}/sources/{source['id']}/auto-sync"

    res = client.patch(url, json={"enabled": True, "interval_minutes": 15}, headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["auto_sync"] is True and body["sync_interval_minutes"] == 15
    assert body["next_sync_at"] is not None

    assert client.patch(url, json={"interval_minutes": 7}, headers=headers).status_code == 422
    assert client.patch(url, json={"enabled": False}, headers=headers).json()["auto_sync"] is False


def test_turning_auto_sync_on_again_ends_a_pause(client, api_owner, db_session_factory):
    headers, project_id = api_owner
    source = create_api_source(client, headers, project_id)
    with db_session_factory() as db:
        row = db.get(Source, source["id"])
        row.auto_sync, row.auto_sync_paused, row.failure_count = True, True, 3
        db.commit()
    url = f"/projects/{project_id}/sources/{source['id']}/auto-sync"
    listed = client.get(f"/projects/{project_id}/sources", headers=headers).json()[0]
    assert listed["auto_sync_paused"] is True and listed["failure_count"] == 3

    body = client.patch(url, json={"enabled": True}, headers=headers).json()
    assert body["auto_sync_paused"] is False and body["failure_count"] == 0


def test_auto_sync_settings_only_for_api_sources_of_own_project(client, api_owner, make_user, db_session_factory):
    headers, project_id = api_owner
    upload = client.post(
        f"/projects/{project_id}/uploads",
        files=[("files", ("chat.txt", b"20/10/26, 10:05 - Karim: Order list lagbe.\n", "text/plain"))],
        headers=headers,
    )
    assert upload.status_code == 200, upload.text
    upload_source = client.get(f"/projects/{project_id}/sources", headers=headers).json()[0]
    res = client.patch(
        f"/projects/{project_id}/sources/{upload_source['id']}/auto-sync", json={"enabled": True}, headers=headers
    )
    assert res.status_code == 400

    source = create_api_source(client, headers, project_id)
    _, other_headers = make_user()
    res = client.patch(
        f"/projects/{project_id}/sources/{source['id']}/auto-sync", json={"enabled": True}, headers=other_headers
    )
    assert res.status_code == 404


def test_listed_source_shows_a_running_sync(client, api_owner, db_session_factory):
    headers, project_id = api_owner
    source = create_api_source(client, headers, project_id)
    with db_session_factory() as db:
        sync.acquire_lock(db, source["id"], sync.utcnow())
    assert client.get(f"/projects/{project_id}/sources", headers=headers).json()[0]["syncing"] is True


def test_project_auto_extract_setting(client, api_owner):
    headers, project_id = api_owner
    assert client.get(f"/projects/{project_id}", headers=headers).json()["auto_extract"] is False
    res = client.patch(f"/projects/{project_id}", json={"auto_extract": True}, headers=headers)
    assert res.status_code == 200 and res.json()["auto_extract"] is True


def test_new_messages_badge(client, api_owner, db_session_factory, script, clock):
    headers, project_id = api_owner
    source = create_api_source(client, headers, project_id)
    url = f"/projects/{project_id}/messages"
    assert client.get(f"{url}/unseen", headers=headers).json() == {"count": 0}

    # A manual sync does not raise the count: the user just saw its result
    script.then([msg("1")], [msg("2"), msg("3")])
    assert client.post(f"/projects/{project_id}/sources/{source['id']}/sync", headers=headers).status_code == 200
    assert client.get(f"{url}/unseen", headers=headers).json() == {"count": 0}

    with db_session_factory() as db:
        row = db.get(Source, source["id"])
        row.auto_sync, row.next_sync_at = True, T0
        db.commit()
    AutoSyncScheduler(db_session_factory, clock, llm_factory=lambda: None).tick()
    assert client.get(f"{url}/unseen", headers=headers).json() == {"count": 2}

    assert client.post(f"{url}/seen", headers=headers).json() == {"count": 0}
    assert client.get(f"{url}/unseen", headers=headers).json() == {"count": 0}
