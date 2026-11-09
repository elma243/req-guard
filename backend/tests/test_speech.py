"""Voice note transcription (app/ai/speech.py) with Groq mocked by respx: no real audio, no network."""

import re
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from app.ai import speech
from app.ai.speech import (
    GROQ_URL,
    PROMPT,
    GroqWhisper,
    RateLimiter,
    TranscriptionNotConfigured,
    TranscriptionUnavailable,
    TranscriptionWorker,
)
from app.connectors.base import AudioAttachment, voice_message
from app.connectors.importer import import_messages
from app.core.config import settings
from app.models import Message, Project, Requirement, Source, User, VoiceAudio
from app.nlp.extraction import extract_requirements

NOW = datetime(2026, 11, 9, 10, 0, tzinfo=timezone.utc)
TRANSCRIPT = "The admin dashboard must show the daily order report."


def groq_ok(text=TRANSCRIPT, language="Bengali", duration=4.2):
    return httpx.Response(200, json={"text": text, "language": language, "duration": duration, "segments": []})


def field(request: httpx.Request, name: str) -> str | None:
    """A form field of a multipart request."""
    match = re.search(rf'name="{name}"\r\n\r\n(.*?)\r\n', request.content.decode("latin-1"), re.DOTALL)
    return match.group(1) if match else None


def whisper(client: httpx.Client | None = None) -> GroqWhisper:
    return GroqWhisper("gsk-test-key", ["whisper-large-v3", "whisper-large-v3-turbo"], client=client)


# ---------- GroqWhisper ----------


@respx.mock
def test_sends_model_prompt_and_language():
    route = respx.post(GROQ_URL).mock(return_value=groq_ok())
    result = whisper().transcribe(b"fake-ogg", "PTT-1.opus", "audio/ogg", "bn")
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer gsk-test-key"
    assert field(request, "model") == "whisper-large-v3"
    assert field(request, "prompt") == PROMPT
    assert field(request, "language") == "bn"
    assert field(request, "response_format") == "verbose_json"
    assert 'filename="PTT-1.ogg"' in request.content.decode("latin-1")  # .opus is sent as .ogg
    assert (result.text, result.language, result.duration, result.model) == (
        TRANSCRIPT, "bengali", 4.2, "whisper-large-v3"
    )


@respx.mock
def test_auto_language_sends_none():
    route = respx.post(GROQ_URL).mock(return_value=groq_ok())
    whisper().transcribe(b"fake", "a.m4a", "audio/mp4", None)
    assert field(route.calls.last.request, "language") is None


@respx.mock
def test_falls_back_to_turbo_when_large_is_busy():
    def answer(request):
        if field(request, "model") == "whisper-large-v3":
            return httpx.Response(503)
        return groq_ok()

    route = respx.post(GROQ_URL).mock(side_effect=answer)
    result = whisper().transcribe(b"fake", "a.ogg", "audio/ogg", None)
    assert result.model == "whisper-large-v3-turbo"
    assert route.call_count == 5  # large: 1 + 3 retries, then turbo once


@respx.mock
def test_429_waits_for_retry_after(monkeypatch):
    waits: list[float] = []
    monkeypatch.setattr(speech, "sleep", waits.append)
    respx.post(GROQ_URL).mock(side_effect=[httpx.Response(429, headers={"Retry-After": "7"}), groq_ok()])
    assert whisper().transcribe(b"fake", "a.ogg", "audio/ogg", None).model == "whisper-large-v3"
    assert waits == [7.0]


@respx.mock
def test_long_retry_after_gives_up_with_the_delay():
    respx.post(GROQ_URL).mock(return_value=httpx.Response(429, headers={"Retry-After": "900"}))
    with pytest.raises(TranscriptionUnavailable) as exc:
        whisper().transcribe(b"fake", "a.ogg", "audio/ogg", None)
    assert exc.value.retry_after == 900


@respx.mock
def test_bad_key_fails_at_once_without_fallback():
    route = respx.post(GROQ_URL).mock(return_value=httpx.Response(401, json={"error": {"message": "bad"}}))
    with pytest.raises(TranscriptionNotConfigured):
        whisper().transcribe(b"fake", "a.ogg", "audio/ogg", None)
    assert route.call_count == 1


def test_rate_limiter_waits_when_the_minute_is_full(monkeypatch):
    now = [100.0]
    waits: list[float] = []
    monkeypatch.setattr(speech, "clock", lambda: now[0])

    def fake_sleep(seconds):
        waits.append(seconds)
        now[0] += seconds

    monkeypatch.setattr(speech, "sleep", fake_sleep)
    limiter = RateLimiter(lambda: 2)
    limiter.acquire()
    now[0] += 10
    limiter.acquire()
    limiter.acquire()  # third in the same minute: waits until the first is 60 s old
    assert waits == [50.0]


# ---------- The pipeline ----------


@pytest.fixture
def db(db_session_factory):
    session = db_session_factory()
    yield session
    session.close()


@pytest.fixture
def project(db):
    user = User(name="Owner", email="owner@example.com", password_hash="x")
    project = Project(name="Acme", owner=user)
    db.add_all([user, project])
    db.commit()
    return project


def add_voice(db, project, file_id="v1", data: bytes | None = b"fake-ogg", source: Source | None = None, **extra):
    attachment = AudioAttachment(file_id=file_id, filename=f"{file_id}.ogg", mime_type="audio/ogg", data=data, **extra)
    msg = voice_message(
        source=source.type if source else "whatsapp", parent_id="m", channel="Acme chat", author="Karim",
        sent_at=NOW - timedelta(hours=1), attachment=attachment, raw={},
    )
    counts = import_messages(db, project.id, source, [msg])
    db.commit()
    return db.get(Message, counts.new_ids[0])


def worker(db_session_factory, clock=lambda: NOW, **kwargs):
    kwargs.setdefault("transcriber_factory", whisper)
    kwargs.setdefault("llm_factory", lambda: None)
    return TranscriptionWorker(db_session_factory, clock, **kwargs)


@respx.mock
def test_tick_stores_transcript_and_deletes_audio(db, project, db_session_factory):
    message = add_voice(db, project, duration=None)
    project.voice_language = "bn"
    db.commit()
    route = respx.post(GROQ_URL).mock(return_value=groq_ok())

    assert worker(db_session_factory).tick() == 1
    db.expire_all()
    stored = db.get(Message, message.id)
    assert (stored.transcript_status, stored.text, stored.transcript_error) == ("done", TRANSCRIPT, None)
    voice = stored.raw["voice"]
    assert (voice["language"], voice["model"], voice["duration"], voice["file_id"]) == (
        "bengali", "whisper-large-v3", 4.2, "v1"
    )
    assert db.get(VoiceAudio, message.id) is None
    assert field(route.calls.last.request, "language") == "bn"
    assert worker(db_session_factory).tick() == 0  # nothing left


@respx.mock
def test_keep_audio_keeps_it(db, project, db_session_factory, monkeypatch):
    monkeypatch.setattr(settings, "keep_audio", True)
    message = add_voice(db, project)
    respx.post(GROQ_URL).mock(return_value=groq_ok())
    worker(db_session_factory).tick()
    db.expire_all()
    assert db.get(VoiceAudio, message.id).data == b"fake-ogg"


@respx.mock
def test_rate_limited_goes_back_to_the_queue_then_fails(db, project, db_session_factory):
    message = add_voice(db, project)
    respx.post(GROQ_URL).mock(return_value=httpx.Response(429, headers={"Retry-After": "90"}))
    clock = [NOW]

    worker(db_session_factory, lambda: clock[0]).tick()
    db.expire_all()
    stored = db.get(Message, message.id)
    assert (stored.transcript_status, stored.transcript_attempts) == ("pending", 1)
    assert "rate limit" in stored.transcript_error
    assert worker(db_session_factory, lambda: clock[0]).tick() == 0  # not due yet

    for _ in range(speech.MAX_ATTEMPTS - 1):
        clock[0] += timedelta(hours=1)
        worker(db_session_factory, lambda: clock[0]).tick()
    db.expire_all()
    stored = db.get(Message, message.id)
    assert (stored.transcript_status, stored.transcript_attempts) == ("failed", speech.MAX_ATTEMPTS)
    assert db.get(VoiceAudio, message.id) is not None  # kept for Retry


def test_not_configured_fails_with_a_clear_message(db, project, db_session_factory, monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", "")
    message = add_voice(db, project)
    TranscriptionWorker(db_session_factory, lambda: NOW, llm_factory=lambda: None).tick()
    db.expire_all()
    stored = db.get(Message, message.id)
    assert stored.transcript_status == "failed"
    assert "GROQ_API_KEY" in stored.transcript_error


def test_upload_without_stored_audio_fails(db, project, db_session_factory):
    message = add_voice(db, project, data=None, size=10)
    worker(db_session_factory).tick()
    db.expire_all()
    assert db.get(Message, message.id).transcript_error == speech.NO_AUDIO


@respx.mock
def test_api_voice_is_downloaded_with_the_sources_connector(db, project, db_session_factory, monkeypatch):
    source = Source(project_id=project.id, type="telegram", display_name="Bot", config={})
    db.add(source)
    db.commit()
    message = add_voice(db, project, data=None, source=source, size=8)
    asked: list[str] = []

    class FakeConnector:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def download_audio(self, attachment):
            asked.append(attachment.file_id)
            return b"downloaded"

    monkeypatch.setattr(speech, "build_connector", lambda s: FakeConnector())
    route = respx.post(GROQ_URL).mock(return_value=groq_ok())
    worker(db_session_factory).tick()
    db.expire_all()
    assert asked == ["v1"]
    assert b"downloaded" in route.calls.last.request.content
    assert db.get(Message, message.id).transcript_status == "done"


def test_stale_claim_is_taken_again(db, project):
    message = add_voice(db, project)
    message.transcript_status = "transcribing"
    message.transcribe_after = NOW - timedelta(minutes=5)
    db.commit()
    assert speech.claim_next(db, NOW) is None  # still being transcribed
    claimed = speech.claim_next(db, NOW + timedelta(minutes=20))
    assert claimed.id == message.id and claimed.transcript_status == "transcribing"


def test_extraction_skips_voice_until_transcribed(db, project):
    message = add_voice(db, project)
    result = extract_requirements(db, project, None, llm_unavailable_reason="test")
    assert result.messages_processed == 0
    db.refresh(message)
    assert message.requirements_extracted_at is None


@respx.mock
def test_transcript_is_auto_extracted_when_the_project_asks(db, project, db_session_factory):
    project.auto_extract = True
    db.commit()
    add_voice(db, project)
    respx.post(GROQ_URL).mock(return_value=groq_ok(language="English"))
    worker(db_session_factory).tick()
    db.expire_all()
    requirements = db.query(Requirement).all()
    assert requirements
    assert requirements[0].sources[0].source_sentence == TRANSCRIPT
