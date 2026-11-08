"""Shared test setup: a fresh in-memory SQLite database for every test, no real .env secrets."""

import os

# Must be set before app.core.config is imported
os.environ["JWT_SECRET"] = "test-secret-not-for-production-0123456789"
# Empty DATABASE_URL = SQLite, so tests never reach the real database configured in backend/.env
os.environ["DATABASE_URL"] = ""
os.environ["PROXY_SHARED_SECRET"] = ""
# backend/.env holds real SMTP settings; tests must never send a real email
os.environ["SMTP_HOST"] = ""
# No background scheduler in tests; test_auto_sync.py drives it with a fake clock
os.environ["AUTO_SYNC_ENABLED"] = "false"

import itertools  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

import app.models  # noqa: E402, F401  (registers models on Base.metadata)
from app.core.db import Base, get_db  # noqa: E402
from app.api.deps import get_optional_llm_client  # noqa: E402
from app.main import app  # noqa: E402
from app.ai import llm  # noqa: E402
from app.connectors import http as connector_http  # noqa: E402
from app.core import email as mailer  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.ratelimit import limiter  # noqa: E402
from app.nlp import nli, similarity  # noqa: E402
from app.nlp.similarity import ModelUnavailable  # noqa: E402
from tests.fake_models import FakeModels  # noqa: E402

API_FIXTURES = Path(__file__).parent / "fixtures" / "api"


def api_json(path: str):
    """A saved sample API response, e.g. api_json("jira/search_page1.json")."""
    return json.loads((API_FIXTURES / path).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def no_retry_wait(monkeypatch):
    """Rate-limit retries in connectors and the LLM client do not really sleep in tests."""
    monkeypatch.setattr(connector_http, "sleep", lambda seconds: None)
    monkeypatch.setattr(llm, "sleep", lambda seconds: None)


@pytest.fixture(autouse=True)
def no_auth_rate_limit(monkeypatch):
    """Tests share one client address; rate limit tests set the real limit themselves."""
    limiter.reset()
    monkeypatch.setattr(settings, "auth_rate_limit_per_minute", 10_000)


@pytest.fixture(autouse=True)
def no_real_llm(override_db):
    """Endpoints get no LLM unless a test sets one: backend/.env may hold a real key."""
    app.dependency_overrides[get_optional_llm_client] = lambda: None


@pytest.fixture(autouse=True)
def fake_models(monkeypatch):
    """The similarity models are always fakes in tests: nothing imports torch or downloads a model.

    test_similarity_models.py (RUN_MODEL_TESTS=1) is the only place that uses the real ones.
    """
    models = FakeModels()

    def model(fake):
        def get():
            if models.unavailable:
                raise ModelUnavailable(models.unavailable)
            return fake

        return get

    monkeypatch.setattr(similarity, "get_embedder", model(models.embedder))
    monkeypatch.setattr(nli, "get_nli", model(models.nli))
    return models


class Outbox(list):
    """Emails the app would have sent, as (to, Email) pairs."""

    def last_to(self, email: str):
        return [message for to, message in self if to == email][-1]

    def code_for(self, email: str) -> str:
        return re.search(r"\b\d{6}\b", self.last_to(email).text).group()

    def reset_token_for(self, email: str) -> str:
        return re.search(r"reset-password\?token=(\S+)", self.last_to(email).text).group(1)


@pytest.fixture(autouse=True)
def outbox(monkeypatch):
    """Captures every email instead of sending it."""
    sent = Outbox()
    monkeypatch.setattr(mailer, "deliver", lambda to, email: sent.append((to, email)))
    return sent


@pytest.fixture
def db_session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    engine.dispose()


@pytest.fixture(autouse=True)
def override_db(db_session_factory):
    def _get_test_db():
        db = db_session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _get_test_db
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def client():
    return TestClient(app)


_counter = itertools.count(1)


@pytest.fixture
def make_user(client, outbox):
    """Register, verify and log in a new user; returns (user_json, auth_headers)."""

    def _make(name: str | None = None, password: str = "password123"):
        n = next(_counter)
        email = f"user{n}@example.com"
        body = {"name": name or f"User {n}", "email": email, "password": password, "confirm_password": password}
        res = client.post("/auth/register", json=body)
        assert res.status_code == 201, res.text
        code = outbox.code_for(email)
        assert client.post("/auth/verify-email", json={"email": email, "code": code}).status_code == 200
        token =client.post("/auth/login", json={"email": email, "password": password}).json()["access_token"]
        return res.json(), {"Authorization": f"Bearer {token}"}

    return _make
