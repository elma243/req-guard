"""Slack connector: channel lookup, history + thread replies, pagination, rate limits, errors."""

from datetime import datetime, timezone

import httpx
import pytest
import respx

from app.connectors.base import AudioAttachment
from app.connectors.http import ConnectorError
from app.connectors.slack import API, SlackConnector, SlackSettings, slack_to_text
from tests.conftest import api_json


def make_connector(*channels: str) -> SlackConnector:
    return SlackConnector(SlackSettings(channels=list(channels) or ["#client-acme"]), token="xoxb-test-token")


def mock_lists(router: respx.Router):
    route = router.get(f"{API}/conversations.list")
    route.side_effect = [
        httpx.Response(200, json=api_json("slack/conversations_list_page1.json")),
        httpx.Response(200, json=api_json("slack/conversations_list_page2.json")),
    ]
    return route


def mock_channel(router: respx.Router):
    mock_lists(router)
    history = router.get(f"{API}/conversations.history")
    history.side_effect = [
        httpx.Response(200, json=api_json("slack/history_page1.json")),
        httpx.Response(200, json=api_json("slack/history_page2.json")),
    ]
    replies = router.get(f"{API}/conversations.replies").respond(json=api_json("slack/replies.json"))
    return history, replies


def test_slack_markup_to_text():
    assert slack_to_text("Hi <@U1>, see <#C1|general> and <https://x.io|the spec> &lt;3") == (
        "Hi @U1, see #general and the spec <3"
    )
    assert slack_to_text("<!here> mail <mailto:a@b.com>") == "@here mail a@b.com"


def test_settings_strip_hash_and_require_a_channel():
    assert SlackSettings(channels=[" #client-acme", "client-acme", "C0GENERAL1"]).channels == [
        "client-acme",
        "C0GENERAL1",
    ]
    with pytest.raises(ValueError):
        SlackSettings(channels=["#", " "])


@respx.mock
def test_fetch_history_threads_and_pagination():
    history, replies = mock_channel(respx.mock)
    with make_connector() as connector:
        messages = connector.fetch()

    # Join message and bot message are skipped; the two thread replies are added
    assert [m.text for m in messages] == [
        "Hi @U0SARAH001, welcome to #client-acme!",
        "The dashboard must load in under 2 seconds & show our KPIs.",
        "Even on mobile?",
        "Yes, on 4G too.",
    ]
    first = messages[1]
    assert first.source == "slack"
    assert first.external_id == "C0ACME0001:1792656000.000200"
    assert first.channel == "#client-acme"
    assert first.author == "Sarah Client"
    assert first.sent_at == datetime.fromtimestamp(1792656000.0002, tz=timezone.utc)
    assert messages[0].author == "U0TOM00001"  # no profile in the message: user id

    assert history.calls[0].request.url.params["channel"] == "C0ACME0001"
    assert history.calls[1].request.url.params["cursor"] == "bmV4dF90czoxNzkyNjU1ODAw"
    assert replies.calls[0].request.url.params["ts"] == "1792656000.000200"
    assert history.calls[0].request.headers["Authorization"] == "Bearer xoxb-test-token"


@respx.mock
def test_channel_by_id_and_since():
    history, _ = mock_channel(respx.mock)
    with make_connector("C0ACME0001") as connector:
        connector.fetch(since=datetime(2026, 10, 21, 0, 0, tzinfo=timezone.utc))
    # 5 minutes margin before `since`
    assert history.calls[0].request.url.params["oldest"] == f"{1792540800 - 300:.6f}"


@respx.mock
def test_rate_limit_is_retried():
    mock_lists(respx.mock)
    history = respx.get(f"{API}/conversations.history")
    history.side_effect = [
        httpx.Response(429, headers={"Retry-After": "1"}),
        httpx.Response(200, json=api_json("slack/history_page2.json")),
    ]
    with make_connector() as connector:
        messages = connector.fetch()
    assert len(messages) == 1
    assert history.call_count == 2


@respx.mock
def test_unknown_channel():
    mock_lists(respx.mock)
    with make_connector("random") as connector, pytest.raises(ConnectorError, match="#random"):
        connector.fetch()


@respx.mock
def test_bot_not_in_channel():
    mock_lists(respx.mock)
    respx.get(f"{API}/conversations.history").respond(json=api_json("slack/error_not_in_channel.json"))
    with make_connector() as connector, pytest.raises(ConnectorError, match="not a member of #client-acme"):
        connector.fetch()


@respx.mock
def test_invalid_token():
    respx.get(f"{API}/conversations.list").respond(json=api_json("slack/error_invalid_auth.json"))
    with make_connector() as connector, pytest.raises(ConnectorError, match="SLACK_BOT_TOKEN") as exc:
        connector.fetch()
    assert "xoxb-test-token" not in str(exc.value)


# ---------- Voice notes ----------

CLIP_URL = "https://files.slack.com/files-pri/T1-F1/download/audio_message.webm"


def _voice_channel(router: respx.Router, scopes: str | None, files: list[dict]):
    lists = api_json("slack/conversations_list_page1.json")
    lists["response_metadata"] = {"next_cursor": ""}
    headers = {"x-oauth-scopes": scopes} if scopes is not None else {}
    router.get(f"{API}/conversations.list").respond(json=lists, headers=headers)
    channel_id = lists["channels"][0]["id"]
    history = {
        "ok": True,
        "messages": [{"type": "message", "subtype": "file_share", "ts": "1792656000.000100", "user": "U1",
                      "text": "", "files": files}],
        "response_metadata": {"next_cursor": ""},
    }
    router.get(f"{API}/conversations.history").respond(json=history, headers=headers)
    return channel_id, lists["channels"][0]["name"]


@respx.mock
def test_audio_clips_and_uploads_become_voice_messages():
    files = [
        {"id": "F1", "name": "audio_message.webm", "mimetype": "audio/webm", "subtype": "slack_audio",
         "duration_ms": 6400, "size": 30000, "url_private_download": CLIP_URL},
        {"id": "F2", "name": "brief.mp3", "mimetype": "audio/mpeg", "size": 90000,
         "url_private_download": "https://files.slack.com/files-pri/T1-F2/download/brief.mp3"},
        {"id": "F3", "name": "mockup.png", "mimetype": "image/png"},
    ]
    channel_id, name = _voice_channel(respx.mock, "channels:read,channels:history,files:read", files)
    with make_connector(f"#{name}") as connector:
        messages = connector.fetch()
    assert [m.external_id for m in messages] == [
        f"{channel_id}:1792656000.000100:audio:F1",
        f"{channel_id}:1792656000.000100:audio:F2",
    ]
    assert all(m.kind == "voice" for m in messages)
    assert (messages[0].audio.duration, messages[0].audio.url) == (6.4, CLIP_URL)
    assert "voice_notice" not in connector.state


@respx.mock
def test_missing_files_read_scope_shows_a_notice_instead_of_failing():
    files = [{"id": "F1", "name": "audio_message.webm", "mimetype": "audio/webm", "subtype": "slack_audio",
              "url_private_download": CLIP_URL}]
    _, name = _voice_channel(respx.mock, "channels:read,channels:history", files)
    with make_connector(f"#{name}") as connector:
        messages = connector.fetch()
    assert len(messages) == 1  # the voice note is kept; it waits for the scope
    assert connector.state["voice_notice"] == "Add files:read to read voice notes"


@respx.mock
def test_download_uses_the_bot_token_and_explains_a_sign_in_page():
    route = respx.get(CLIP_URL).respond(content=b"fake-webm", headers={"content-type": "audio/webm"})
    attachment = AudioAttachment(file_id="F1", filename="a.webm", mime_type="audio/webm", url=CLIP_URL)
    with make_connector() as connector:
        assert connector.download_audio(attachment) == b"fake-webm"
    assert route.calls[0].request.headers["Authorization"] == "Bearer xoxb-test-token"

    respx.get(CLIP_URL).respond(html="<html>Sign in to Slack</html>")
    with make_connector() as connector, pytest.raises(ConnectorError) as exc:
        connector.download_audio(attachment)
    assert "Add files:read to read voice notes" in str(exc.value)
