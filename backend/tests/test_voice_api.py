"""Voice notes through the API: uploads, the message fields, Retry, the audio endpoint and the voice language."""

import io
import zipfile

import pytest

from app.api import sources as sources_api
from app.models import Message, VoiceAudio


@pytest.fixture
def owner(client, make_user):
    _, headers = make_user()
    project = client.post("/projects", json={"name": "Acme"}, headers=headers).json()
    return headers, f"/projects/{project['id']}"


def upload(client, headers, base, *files: tuple[str, bytes]):
    return client.post(
        f"{base}/uploads",
        files=[("files", (name, data, "application/octet-stream")) for name, data in files],
        headers=headers,
    )


# ---------- uploads ----------


def test_voice_note_upload_is_queued(client, owner, db_session_factory):
    headers, base = owner
    body = upload(client, headers, base, ("brief.m4a", b"fake-m4a"), ("call.opus", b"fake-opus")).json()

    assert (body["imported"], body["voice_queued"], body["voice_skipped"]) == (2, 2, 0)
    assert [f["type"] for f in body["files"]] == ["voice_upload", "voice_upload"]
    sources = client.get(f"{base}/sources", headers=headers).json()
    assert [(s["type"], s["display_name"], s["message_count"]) for s in sources] == [
        ("voice_upload", "Voice notes", 2)
    ]
    with db_session_factory() as db:
        stored = db.query(Message).order_by(Message.id).all()
        assert [m.transcript_status for m in stored] == ["pending", "pending"]
        assert db.get(VoiceAudio, stored[0].id).data == b"fake-m4a"

    again = upload(client, headers, base, ("renamed.m4a", b"fake-m4a")).json()
    assert (again["imported"], again["duplicates"]) == (0, 1)  # same audio = same voice note


@pytest.mark.parametrize("name", ["note.mp3", "note.wav", "note.webm", "note.ogg"])
def test_accepted_audio_types(client, owner, name):
    headers, base = owner
    assert upload(client, headers, base, (name, b"fake")).json()["voice_queued"] == 1


def test_size_limits_per_file_type(client, owner, monkeypatch):
    headers, base = owner
    monkeypatch.setattr(sources_api, "MAX_VOICE_BYTES", 10)
    monkeypatch.setattr(sources_api, "MAX_ZIP_BYTES", 20)
    monkeypatch.setattr(sources_api, "MAX_FILE_BYTES", 5)
    body = upload(
        client, headers, base, ("big.m4a", b"x" * 11), ("big.zip", b"x" * 21), ("notes.txt", b"x" * 6),
        ("ok.m4a", b"x" * 10),
    ).json()
    assert [f["error"] for f in body["files"]] == [
        "Voice notes can be at most 25 MB.",
        "ZIP files can be at most 100 MB.",
        "File is larger than 10 MB.",
        None,
    ]


def test_whatsapp_zip_upload(client, owner):
    headers, base = owner
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr(
            "WhatsApp Chat with Acme.txt",
            "12/03/24, 14:00 - Sarah: Login page lagbe\n"
            "12/03/24, 14:05 - Sarah: PTT-20240312-WA0003.opus (file attached)\n"
            "12/03/24, 14:06 - Sarah: PTT-20240312-WA0004.opus (file attached)\n",
        )
        zf.writestr("PTT-20240312-WA0003.opus", b"fake")
    body = upload(client, headers, base, ("WhatsApp Chat with Acme.zip", buffer.getvalue())).json()
    assert body["files"][0]["type"] == "whatsapp"
    assert (body["imported"], body["voice_queued"], body["voice_skipped"]) == (3, 1, 1)


# ---------- messages ----------


def _set(db_session_factory, **values):
    """Change the only voice message of the test, as the worker would."""
    with db_session_factory() as db:
        message = db.query(Message).filter(Message.kind == "voice").one()
        for key, value in values.items():
            setattr(message, key, value)
        db.commit()
        return message.id


def test_messages_show_voice_fields(client, owner, db_session_factory):
    headers, base = owner
    upload(client, headers, base, ("brief.m4a", b"fake-m4a"), ("notes.md", b"Login page lagbe."))
    items = client.get(f"{base}/messages", headers=headers).json()["items"]
    voice = next(m for m in items if m["kind"] == "voice")
    text = next(m for m in items if m["kind"] == "text")
    assert (voice["text"], voice["transcript_status"], voice["has_audio"]) == ("", "pending", True)
    assert voice["voice"]["filename"] == "brief.m4a"
    assert (text["transcript_status"], text["has_audio"], text["voice"]) == (None, False, None)


def test_retry_queues_a_failed_voice_note(client, owner, db_session_factory):
    headers, base = owner
    upload(client, headers, base, ("brief.m4a", b"fake-m4a"))
    message_id = _set(db_session_factory, transcript_status="failed", transcript_error="Groq is busy",
                      transcript_attempts=5)

    res = client.post(f"{base}/messages/{message_id}/transcribe", headers=headers)
    assert res.status_code == 200, res.text
    assert (res.json()["transcript_status"], res.json()["transcript_error"]) == ("pending", None)
    with db_session_factory() as db:
        assert db.get(Message, message_id).transcript_attempts == 0

    _set(db_session_factory, transcript_status="done", text="Transcript")
    assert client.post(f"{base}/messages/{message_id}/transcribe", headers=headers).status_code == 409
    _set(db_session_factory, transcript_status="skipped", transcript_error="Too large")
    res = client.post(f"{base}/messages/{message_id}/transcribe", headers=headers)
    assert res.status_code == 400 and "Too large" in res.json()["detail"]


def test_retry_only_for_voice_notes(client, owner):
    headers, base = owner
    upload(client, headers, base, ("notes.md", b"Login page lagbe."))
    message_id = client.get(f"{base}/messages", headers=headers).json()["items"][0]["id"]
    assert client.post(f"{base}/messages/{message_id}/transcribe", headers=headers).status_code == 400


def test_audio_endpoint_plays_stored_audio(client, owner, make_user, db_session_factory):
    headers, base = owner
    upload(client, headers, base, ("brief.m4a", b"fake-m4a"))
    message_id = client.get(f"{base}/messages", headers=headers).json()["items"][0]["id"]

    res = client.get(f"{base}/messages/{message_id}/audio", headers=headers)
    assert res.status_code == 200
    assert (res.content, res.headers["content-type"]) == (b"fake-m4a", "audio/mp4")

    _, stranger = make_user()
    assert client.get(f"{base}/messages/{message_id}/audio", headers=stranger).status_code == 404
    with db_session_factory() as db:
        db.delete(db.get(VoiceAudio, message_id))
        db.commit()
    assert client.get(f"{base}/messages/{message_id}/audio", headers=headers).status_code == 404


def test_deleting_a_voice_message_deletes_its_audio(client, owner, db_session_factory):
    headers, base = owner
    upload(client, headers, base, ("brief.m4a", b"fake-m4a"))
    message_id = client.get(f"{base}/messages", headers=headers).json()["items"][0]["id"]
    assert client.delete(f"{base}/messages/{message_id}", headers=headers).status_code == 204
    with db_session_factory() as db:
        assert db.query(VoiceAudio).count() == 0


# ---------- project setting and requirement sources ----------


def test_voice_language_setting(client, owner):
    headers, base = owner
    assert client.get(base, headers=headers).json()["voice_language"] == "auto"
    res = client.patch(base, json={"voice_language": "bn"}, headers=headers)
    assert res.status_code == 200 and res.json()["voice_language"] == "bn"
    assert client.patch(base, json={"voice_language": "fr"}, headers=headers).status_code == 422


def test_requirement_from_a_transcript_shows_its_voice_source(client, owner, db_session_factory):
    headers, base = owner
    upload(client, headers, base, ("brief.m4a", b"fake-m4a"))
    _set(db_session_factory, transcript_status="done", text="The admin dashboard must show the daily order report.",
         raw={"voice": {"filename": "brief.m4a", "duration": 4.2, "language": "english", "model": "whisper-large-v3"}})

    assert client.post(f"{base}/requirements/extract", headers=headers).status_code == 200
    requirements = client.get(f"{base}/requirements", headers=headers).json()
    assert requirements and requirements[0]["sources"][0]["message_kind"] == "voice"
    detail = client.get(f"{base}/requirements/{requirements[0]['id']}", headers=headers).json()
    source = detail["sources"][0]
    assert (source["has_audio"], source["voice"]["duration"], source["voice"]["language"]) == (True, 4.2, "english")


# ---------- audio player: type, length and byte ranges ----------


def _stored_audio(client, owner, name: str, data: bytes, db_session_factory, mime: str | None = None) -> str:
    headers, base = owner
    upload(client, headers, base, (name, data))
    message_id = client.get(f"{base}/messages", headers=headers).json()["items"][0]["id"]
    if mime:
        with db_session_factory() as db:
            db.get(VoiceAudio, message_id).mime_type = mime
            db.commit()
    return f"{base}/messages/{message_id}/audio"


def test_audio_supports_byte_ranges(client, owner, db_session_factory):
    headers, _ = owner
    url = _stored_audio(client, owner, "voice.ogg", b"0123456789", db_session_factory)

    full = client.get(url, headers=headers)
    assert (full.status_code, full.headers["accept-ranges"], full.headers["content-length"]) == (200, "bytes", "10")

    part = client.get(url, headers={**headers, "Range": "bytes=2-5"})
    assert (part.status_code, part.content, part.headers["content-range"]) == (206, b"2345", "bytes 2-5/10")
    assert part.headers["content-length"] == "4"

    open_end = client.get(url, headers={**headers, "Range": "bytes=7-"})
    assert (open_end.status_code, open_end.content) == (206, b"789")
    suffix = client.get(url, headers={**headers, "Range": "bytes=-3"})
    assert (suffix.status_code, suffix.content, suffix.headers["content-range"]) == (206, b"789", "bytes 7-9/10")
    past_end = client.get(url, headers={**headers, "Range": "bytes=20-"})
    assert (past_end.status_code, past_end.headers["content-range"]) == (416, "bytes */10")


@pytest.mark.parametrize(
    "name,stored_type,served",
    [
        ("file_7.oga", "audio/ogg", "audio/ogg"),  # Telegram voice
        ("voice.opus", "audio/opus", "audio/ogg"),
        ("brief.m4a", "audio/x-m4a", "audio/mp4"),
        ("call.mp3", "audio/mp3", "audio/mpeg"),
        ("clip.webm", "audio/webm", "audio/webm"),
    ],
)
def test_audio_is_served_with_a_playable_type(client, owner, db_session_factory, name, stored_type, served):
    headers, _ = owner
    upload_name = name if not name.endswith(".oga") else "file_7.ogg"  # .oga is not an upload type
    url = _stored_audio(client, owner, upload_name, b"fake", db_session_factory, mime=stored_type)
    assert client.get(url, headers=headers).headers["content-type"] == served
