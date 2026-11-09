"""Telegram connector: getUpdates offset handling, group filtering, errors. All HTTP is mocked."""

from datetime import datetime, timezone

import pytest
import respx

from app.connectors.base import AudioAttachment
from app.connectors.http import ConnectorError
from app.connectors.telegram import TelegramConnector, TelegramSettings
from tests.conftest import api_json

TOKEN = "123456:TEST-token"
URL = f"https://api.telegram.org/bot{TOKEN}/getUpdates"


def make_connector(chat_ids: list[int] | None = None, state: dict | None = None) -> TelegramConnector:
    return TelegramConnector(TelegramSettings(chat_ids=chat_ids or []), token=TOKEN, state=state)


@respx.mock
def test_fetch_group_messages_and_remember_last_update():
    route = respx.get(URL).respond(json=api_json("telegram/get_updates.json"))
    with make_connector() as connector:
        messages = connector.fetch()

    # Private chat and the service message (new member) are skipped; photo caption is kept
    assert [m.text for m in messages] == [
        "Users should be able to pay with bKash and cards.",
        "This is the checkout screen they want.",
        "Unrelated group",
    ]
    first = messages[0]
    assert first.source == "telegram"
    assert first.external_id == "-1001234567890:41"
    assert first.channel == "Acme project"
    assert first.author == "Sarah Client"
    assert first.sent_at == datetime.fromtimestamp(1792656000, tz=timezone.utc)
    assert messages[1].author == "@tom_pm"

    # Fewer updates than the page size: one call; state holds the highest update_id
    assert route.call_count == 1
    assert "offset" not in route.calls[0].request.url.params
    assert connector.state == {"last_update_id": 503900005}


@respx.mock
def test_next_sync_continues_after_stored_update_id():
    route = respx.get(URL).respond(json=api_json("telegram/get_updates_empty.json"))
    with make_connector(state={"last_update_id": 503900005}) as connector:
        assert connector.fetch() == []
    assert route.calls[0].request.url.params["offset"] == "503900006"
    assert connector.state == {"last_update_id": 503900005}


@respx.mock
def test_full_page_fetches_the_next_one():
    sample = api_json("telegram/get_updates.json")["result"][0]
    page = [
        {"update_id": 1000 + i, "message": {**sample["message"], "message_id": i, "text": f"message {i}"}}
        for i in range(100)
    ]
    route = respx.get(URL)
    route.side_effect = [
        respx.MockResponse(200, json={"ok": True, "result": page}),
        respx.MockResponse(200, json=api_json("telegram/get_updates_empty.json")),
    ]
    with make_connector() as connector:
        assert len(connector.fetch()) == 100
    assert route.calls[1].request.url.params["offset"] == "1100"
    assert connector.state == {"last_update_id": 1099}


@respx.mock
def test_chat_filter():
    respx.get(URL).respond(json=api_json("telegram/get_updates.json"))
    with make_connector(chat_ids=[-1001234567890]) as connector:
        messages = connector.fetch()
    assert {m.channel for m in messages} == {"Acme project"}


def test_chat_ids_accept_strings_from_the_form():
    assert TelegramSettings(chat_ids=["-1001234567890"]).chat_ids == [-1001234567890]
    with pytest.raises(ValueError):
        TelegramSettings(chat_ids=["acme"])


@respx.mock
def test_webhook_conflict():
    respx.get(URL).respond(409, json=api_json("telegram/error_conflict.json"))
    with make_connector() as connector, pytest.raises(ConnectorError, match="webhook"):
        connector.fetch()


@respx.mock
def test_wrong_token_message_does_not_contain_token():
    respx.get(URL).respond(401, json=api_json("telegram/error_unauthorized.json"))
    with make_connector() as connector, pytest.raises(ConnectorError, match="TELEGRAM_BOT_TOKEN") as exc:
        connector.fetch()
    assert TOKEN not in str(exc.value)


# ---------- Voice notes ----------

FILE_URL = f"https://api.telegram.org/file/bot{TOKEN}"


def _update(update_id: int, **content) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "date": 1792656000,
            "chat": {"id": -100, "title": "Acme project", "type": "supergroup"},
            "from": {"first_name": "Karim"},
            **content,
        },
    }


@respx.mock
def test_voice_audio_and_audio_documents_become_voice_messages():
    updates = [
        _update(1, voice={"file_id": "VOICE1", "file_unique_id": "u1", "duration": 7, "mime_type": "audio/ogg",
                          "file_size": 12000}),
        _update(2, caption="See the clip", audio={"file_id": "AUD2", "file_name": "brief.m4a",
                                                  "mime_type": "audio/mp4", "duration": 30}),
        _update(3, document={"file_id": "DOC3", "file_name": "spec.pdf", "mime_type": "application/pdf"}),
        _update(4, document={"file_id": "DOC4", "file_name": "call.mp3", "mime_type": "audio/mpeg"}),
        _update(5, voice={"file_id": "BIG5", "file_size": 21 * 1024 * 1024}),
    ]
    respx.get(URL).respond(json={"ok": True, "result": updates})
    with make_connector() as connector:
        messages = connector.fetch()

    voice = [m for m in messages if m.kind == "voice"]
    assert [m.external_id for m in voice] == [
        "-100:1:audio:VOICE1", "-100:2:audio:AUD2", "-100:4:audio:DOC4", "-100:5:audio:BIG5"
    ]
    assert [m.text for m in messages if m.kind == "text"] == ["See the clip"]  # the caption stays a text message
    first = voice[0]
    assert (first.audio.filename, first.audio.mime_type, first.audio.duration, first.author) == (
        "voice-1.ogg", "audio/ogg", 7, "Karim"
    )
    assert first.raw["parent_external_id"] == "-100:1"
    assert voice[3].audio.skip_reason is not None and "20 MB" in voice[3].audio.skip_reason
    assert TOKEN not in str([m.model_dump() for m in messages])


@respx.mock
def test_download_audio_uses_get_file():
    get_file = respx.get(f"https://api.telegram.org/bot{TOKEN}/getFile").respond(
        json={"ok": True, "result": {"file_id": "VOICE1", "file_path": "voice/file_7.oga"}}
    )
    respx.get(f"{FILE_URL}/voice/file_7.oga").respond(content=b"fake-ogg", headers={"content-type": "audio/ogg"})
    with make_connector() as connector:
        data = connector.download_audio(AudioAttachment(file_id="VOICE1", filename="v.ogg", mime_type="audio/ogg"))
    assert data == b"fake-ogg"
    assert get_file.calls[0].request.url.params["file_id"] == "VOICE1"


@respx.mock
def test_download_audio_too_big_for_bots():
    respx.get(f"https://api.telegram.org/bot{TOKEN}/getFile").respond(
        400, json={"ok": False, "description": "Bad Request: file is too big"}
    )
    with make_connector() as connector, pytest.raises(ConnectorError) as exc:
        connector.download_audio(AudioAttachment(file_id="X", filename="v.ogg", mime_type="audio/ogg"))
    assert "20 MB" in str(exc.value) and TOKEN not in str(exc.value)
