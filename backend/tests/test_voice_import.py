"""The shared voice note pieces: audio detection, voice messages, storing them and the capped download."""

from datetime import datetime, timezone

import httpx
import pytest
import respx

from app.connectors import http
from app.connectors.base import MAX_AUDIO_BYTES, AudioAttachment, audio_mime, voice_message
from app.connectors.http import AudioTooLargeError, ConnectorError, NotAudioError, download
from app.connectors.importer import import_messages
from app.models import Message, Project, User, VoiceAudio

SENT = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
URL = "https://files.example.com/voice.ogg"


@pytest.mark.parametrize(
    "filename,mime,expected",
    [
        ("note.ogg", "", "audio/ogg"),
        ("PTT-20240312-WA0003.opus", "application/octet-stream", "audio/ogg"),
        ("clip.mp4", "video/mp4", "audio/mp4"),  # Messenger voice clips
        ("x.bin", "audio/webm; codecs=opus", "audio/webm"),
        ("photo.jpg", "image/jpeg", None),
        ("notes.txt", "", None),
    ],
)
def test_audio_mime(filename, mime, expected):
    assert audio_mime(filename, mime) == expected


def _voice(file_id="f1", data: bytes | None = b"fake-ogg", **extra):
    attachment = AudioAttachment(file_id=file_id, filename="voice.ogg", mime_type="audio/ogg", data=data, **extra)
    return voice_message(
        source="telegram", parent_id="chat:7", channel="Acme", author="Karim", sent_at=SENT,
        attachment=attachment, raw={"chat_id": 1},
    )


def test_voice_message_has_its_own_stable_id():
    msg = _voice()
    assert msg.external_id == "chat:7:audio:f1"
    assert msg.kind == "voice" and msg.text == ""
    assert msg.raw == {"chat_id": 1, "parent_external_id": "chat:7"}
    assert "data" not in msg.audio.metadata()


@pytest.fixture
def project(db_session_factory):
    db = db_session_factory()
    user = User(name="Owner", email="owner@example.com", password_hash="x")
    project = Project(name="Acme", owner=user)
    db.add_all([user, project])
    db.commit()
    yield db, project
    db.close()


def test_import_queues_voice_and_stores_inline_audio(project):
    db, proj = project
    counts = import_messages(db, proj.id, None, [_voice(duration=4.5)])
    db.commit()
    assert (counts.imported, counts.voice_queued, counts.voice_skipped) == (1, 1, 0)
    stored = db.query(Message).one()
    assert (stored.kind, stored.text, stored.transcript_status) == ("voice", "", "pending")
    assert stored.raw["voice"]["file_id"] == "f1"
    assert stored.raw["voice"]["duration"] == 4.5
    audio = db.get(VoiceAudio, stored.id)
    assert audio.data == b"fake-ogg" and audio.size_bytes == 8

    again = import_messages(db, proj.id, None, [_voice()])
    assert (again.imported, again.duplicates) == (0, 1)


def test_import_skips_too_large_and_known_unreadable_voice(project):
    db, proj = project
    big = _voice("big", data=None, size=MAX_AUDIO_BYTES + 1)
    missing = _voice("gone", data=None, skip_reason="Audio file not in the upload.")
    counts = import_messages(db, proj.id, None, [big, missing])
    db.commit()
    assert (counts.voice_queued, counts.voice_skipped) == (0, 2)
    rows = {m.external_id: m for m in db.query(Message)}
    assert rows["chat:7:audio:big"].transcript_status == "skipped"
    assert "25 MB" in rows["chat:7:audio:big"].transcript_error
    assert rows["chat:7:audio:gone"].transcript_error == "Audio file not in the upload."
    assert db.query(VoiceAudio).count() == 0


def test_api_voice_without_inline_data_is_queued_without_audio(project):
    db, proj = project
    import_messages(db, proj.id, None, [_voice(data=None, size=1000)])
    db.commit()
    assert db.query(Message).one().transcript_status == "pending"
    assert db.query(VoiceAudio).count() == 0


@respx.mock
def test_download_returns_bytes_and_follows_redirects():
    respx.get(URL).mock(return_value=httpx.Response(302, headers={"Location": "https://cdn.example.com/v.ogg"}))
    respx.get("https://cdn.example.com/v.ogg").mock(
        return_value=httpx.Response(200, content=b"fake-ogg", headers={"content-type": "audio/ogg"})
    )
    with httpx.Client() as client:
        assert download(client, URL, service="Example") == b"fake-ogg"


@respx.mock
def test_download_stops_at_the_size_limit():
    respx.get(URL).mock(return_value=httpx.Response(200, content=b"x" * 20))
    with httpx.Client() as client, pytest.raises(AudioTooLargeError):
        download(client, URL, service="Example", max_bytes=10)


@respx.mock
def test_download_rejects_a_web_page():
    respx.get(URL).mock(return_value=httpx.Response(200, html="<html>Sign in</html>"))
    with httpx.Client() as client, pytest.raises(NotAudioError):
        download(client, URL, service="Example")


@respx.mock
def test_download_retries_429_and_hides_the_url(monkeypatch):
    waits: list[float] = []
    monkeypatch.setattr(http, "sleep", waits.append)
    respx.get(URL).mock(
        side_effect=[httpx.Response(429, headers={"Retry-After": "2"}), httpx.Response(200, content=b"ok")]
    )
    with httpx.Client() as client:
        assert download(client, URL, service="Example") == b"ok"
    assert waits == [2.0]

    respx.get(URL).mock(return_value=httpx.Response(404))
    with httpx.Client() as client, pytest.raises(ConnectorError) as exc:
        download(client, URL, service="Example")
    assert URL not in str(exc.value)
