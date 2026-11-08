"""Automatic sync of API sources: the per-source lock, the scheduler (with a fake clock), the pause after errors,
the Gmail reconnect state, auto-extract and the new-messages count.

No external API is called: `build_connector` is replaced by a fake connector that returns given messages or
raises a given error.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.connectors import sync
from app.connectors.scheduler import AutoSyncScheduler, spread_offset
from app.connectors.base import NormalizedMessage
from app.connectors.http import ConnectorError
from app.core.config import settings
from app.models import Message, Project, Source, User

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
    return AutoSyncScheduler(db_session_factory, clock)


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
    scheduler = AutoSyncScheduler(db_session_factory, clock, max_per_tick=3)
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
