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
