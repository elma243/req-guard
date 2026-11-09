"""Discord connector: guild channels, forward pagination with `after`, rate limits, errors."""

from datetime import datetime, timezone

import httpx
import pytest
import respx

from app.connectors.base import AudioAttachment
from app.connectors.discord import API, DiscordConnector, DiscordSettings
from app.connectors.http import ConnectorError
from tests.conftest import api_json

GUILD = "1280000000000000000"
ACME = "1290000000000000010"
RANDOM = "1290000000000000020"


def make_connector(channels: list[str] | None = None, state: dict | None = None) -> DiscordConnector:
    return DiscordConnector(
        DiscordSettings(guild_id=GUILD, channels=channels or []), token="discord-test-token", state=state
    )


def mock_guild(router: respx.Router):
    router.get(f"{API}/guilds/{GUILD}/channels").respond(json=api_json("discord/guild_channels.json"))
    acme = router.get(f"{API}/channels/{ACME}/messages").respond(json=api_json("discord/messages_client_acme.json"))
    random = router.get(f"{API}/channels/{RANDOM}/messages").respond(json=api_json("discord/messages_random.json"))
    return acme, random


def test_settings():
    assert DiscordSettings(guild_id=f" {GUILD} ", channels=["#client-acme", "client-acme"]).channels == ["client-acme"]
    with pytest.raises(ValueError):
        DiscordSettings(guild_id="my-server")


@respx.mock
def test_fetch_all_text_channels():
    acme, random = mock_guild(respx.mock)
    with make_connector() as connector:
        messages = connector.fetch()

    # Join message (type 7) and bot message are skipped; voice and category channels are not read
    assert [m.text for m in messages] == [
        "Admins need to export the user list as CSV.",
        "Which file formats for the export?",
        "Lunch?",
    ]
    first = messages[0]
    assert first.source == "discord"
    assert first.external_id == "1300000000000000101"
    assert first.channel == "#client-acme"
    assert first.author == "Sarah Client"
    assert first.sent_at == datetime(2026, 10, 22, 8, 0, 0, 123000, tzinfo=timezone.utc)
    assert messages[1].author == "tom_pm"

    assert acme.calls[0].request.url.params["after"] == "0"
    assert acme.calls[0].request.headers["Authorization"] == "Bot discord-test-token"
    # Newest id per channel, including skipped messages, so they are not read again
    assert connector.state == {
        "last_message_ids": {ACME: "1300000000000000104", RANDOM: "1300000000000000201"}
    }


@respx.mock
def test_channel_filter_and_stored_position():
    acme, random = mock_guild(respx.mock)
    with make_connector(["#client-acme"], state={"last_message_ids": {ACME: "1300000000000000100"}}) as connector:
        connector.fetch()
    assert acme.calls[0].request.url.params["after"] == "1300000000000000100"
    assert not random.called


@respx.mock
def test_pagination_and_rate_limit():
    respx.get(f"{API}/guilds/{GUILD}/channels").respond(json=api_json("discord/guild_channels.json"))
    sample = api_json("discord/messages_client_acme.json")[3]
    full_page = [{**sample, "id": str(1300000000000001000 + i), "content": f"m{i}"} for i in range(100)]
    route = respx.get(f"{API}/channels/{ACME}/messages")
    route.side_effect = [
        httpx.Response(200, json=full_page),
        httpx.Response(429, json={"message": "You are being rate limited.", "retry_after": 0.25, "global": False}),
        httpx.Response(200, json=[]),
    ]
    with make_connector(["client-acme"]) as connector:
        assert len(connector.fetch()) == 100
    assert route.calls[2].request.url.params["after"] == "1300000000000001099"


@respx.mock
def test_unknown_channel():
    respx.get(f"{API}/guilds/{GUILD}/channels").respond(json=api_json("discord/guild_channels.json"))
    with make_connector(["general"]) as connector, pytest.raises(ConnectorError, match="#general"):
        connector.fetch()


@respx.mock
def test_missing_message_content_intent():
    respx.get(f"{API}/guilds/{GUILD}/channels").respond(json=api_json("discord/guild_channels.json"))
    respx.get(f"{API}/channels/{ACME}/messages").respond(json=api_json("discord/messages_no_content.json"))
    with make_connector(["client-acme"]) as connector, pytest.raises(ConnectorError, match="Message Content intent"):
        connector.fetch()


@respx.mock
def test_bot_without_access():
    respx.get(f"{API}/guilds/{GUILD}/channels").respond(403, json={"message": "Missing Access", "code": 50001})
    with make_connector() as connector, pytest.raises(ConnectorError, match="DISCORD_BOT_TOKEN"):
        connector.fetch()


# ---------- Voice notes ----------

CDN = "https://cdn.discordapp.com/attachments/1290000000000000010"


def _message(message_id: str, content: str = "", flags: int = 0, attachments: list | None = None) -> dict:
    return {
        "id": message_id,
        "type": 0,
        "content": content,
        "flags": flags,
        "timestamp": "2026-10-20T10:00:00+00:00",
        "author": {"id": "1", "username": "karim"},
        "attachments": attachments or [],
    }


@respx.mock
def test_voice_messages_and_audio_attachments():
    respx.get(f"{API}/guilds/{GUILD}/channels").respond(json=api_json("discord/guild_channels.json"))
    page = [
        _message("1300000000000000001", flags=8192, attachments=[
            {"id": "11", "filename": "voice-message.ogg", "content_type": "audio/ogg", "size": 9000,
             "duration_secs": 5.5, "url": f"{CDN}/11/voice-message.ogg?ex=1"},
        ]),
        _message("1300000000000000002", content="Brief attached", attachments=[
            {"id": "12", "filename": "brief.m4a", "size": 50000, "url": f"{CDN}/12/brief.m4a"},
            {"id": "13", "filename": "screen.png", "content_type": "image/png", "url": f"{CDN}/13/screen.png"},
        ]),
    ]
    respx.get(f"{API}/channels/{ACME}/messages").respond(json=page)
    with make_connector(channels=["client-acme"]) as connector:
        messages = connector.fetch()

    assert [(m.kind, m.external_id) for m in messages] == [
        ("voice", "1300000000000000001:audio:11"),
        ("text", "1300000000000000002"),
        ("voice", "1300000000000000002:audio:12"),
    ]
    voice = messages[0].audio
    assert (voice.mime_type, voice.duration, voice.ref["voice_message"]) == ("audio/ogg", 5.5, True)
    assert messages[2].audio.mime_type == "audio/mp4"


@respx.mock
def test_download_audio_refreshes_an_expired_url():
    expired = f"{CDN}/11/voice-message.ogg?ex=old"
    fresh = f"{CDN}/11/voice-message.ogg?ex=new"
    respx.get(expired).respond(404)
    respx.get(f"{API}/channels/{ACME}/messages/1300000000000000001").respond(
        json=_message("1300000000000000001", attachments=[{"id": "11", "url": fresh}])
    )
    respx.get(fresh).respond(content=b"fake-ogg", headers={"content-type": "audio/ogg"})
    attachment = AudioAttachment(
        file_id="11", filename="voice-message.ogg", mime_type="audio/ogg", url=expired,
        ref={"channel_id": ACME, "message_id": "1300000000000000001"},
    )
    with make_connector() as connector:
        assert connector.download_audio(attachment) == b"fake-ogg"
