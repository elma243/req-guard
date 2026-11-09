"""Gmail connector: OAuth URL and code exchange, token refresh, threads → plain-text messages."""

from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

from app.connectors.gmail import (
    API,
    TOKEN_URL,
    GmailConnector,
    GmailSettings,
    authorize_url,
    exchange_code,
)
from app.connectors.base import AudioAttachment
from app.connectors.http import ConnectorError, NeedsAuthError
from tests.conftest import api_json


def make_connector(query: str = "from:acme.example.com") -> GmailConnector:
    return GmailConnector(
        GmailSettings(query=query), client_id="cid", client_secret="csecret", refresh_token="1//test-refresh-token"
    )


def mock_mailbox(router: respx.Router):
    token = router.post(TOKEN_URL).respond(json=api_json("gmail/token_refresh.json"))
    threads = router.get(f"{API}/threads")
    threads.side_effect = [
        httpx.Response(200, json=api_json("gmail/threads_page1.json")),
        httpx.Response(200, json=api_json("gmail/threads_page2.json")),
    ]
    router.get(f"{API}/threads/18f0a1b2c3d4e5f6").respond(json=api_json("gmail/thread_1.json"))
    router.get(f"{API}/threads/18f0aaaaaaaaaaaa").respond(json=api_json("gmail/thread_2.json"))
    return token, threads


def form(request: httpx.Request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}


def test_authorize_url_asks_for_offline_read_only_access():
    url = urlparse(authorize_url("state-123", client_id="cid", redirect_uri="http://localhost:8000/cb"))
    params = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert url.netloc == "accounts.google.com"
    assert params["scope"] == "https://www.googleapis.com/auth/gmail.readonly"
    assert params["access_type"] == "offline"
    assert params["prompt"] == "consent"
    assert params["state"] == "state-123"
    assert params["redirect_uri"] == "http://localhost:8000/cb"


def test_query_is_required():
    assert GmailSettings(query="  from:a@b.com   label:x ").query == "from:a@b.com label:x"
    with pytest.raises(ValueError):
        GmailSettings(query="   ")


@respx.mock
def test_exchange_code_returns_refresh_token():
    route = respx.post(TOKEN_URL).respond(json=api_json("gmail/token_exchange.json"))
    token = exchange_code("auth-code", client_id="cid", client_secret="csecret", redirect_uri="http://cb")
    assert token == "1//test-refresh-token"
    sent = form(route.calls[0].request)
    assert sent["grant_type"] == "authorization_code"
    assert sent["code"] == "auth-code"


@respx.mock
def test_exchange_code_without_refresh_token():
    respx.post(TOKEN_URL).respond(json=api_json("gmail/token_refresh.json"))
    with pytest.raises(ConnectorError, match="did not return a refresh token"):
        exchange_code("auth-code", client_id="cid", client_secret="csecret", redirect_uri="http://cb")


@respx.mock
def test_fetch_threads_as_plain_text():
    token, threads = mock_mailbox(respx.mock)
    with make_connector() as connector:
        messages = connector.fetch()

    assert form(token.calls[0].request)["grant_type"] == "refresh_token"
    assert threads.calls[0].request.headers["Authorization"] == "Bearer ya29.test-access-token"
    assert threads.calls[0].request.url.params["q"] == "from:acme.example.com"
    assert threads.calls[1].request.url.params["pageToken"] == "next-page-1"

    # Draft skipped; 3 messages from 2 threads
    assert [m.external_id for m in messages] == ["18f0a1b2c3d4e5f6", "18f0a1b2c3d4e600", "18f0aaaaaaaaaaaa"]
    first, reply, html_mail = messages

    assert first.source == "gmail"
    assert first.channel == "Invoice export"
    assert first.author == "Sarah Client"
    assert first.sent_at == datetime(2026, 10, 22, 8, 0, tzinfo=timezone.utc)
    # text/plain preferred; signature removed; subject only on the first message of a thread
    assert first.text == "Invoice export\n\nHi team,\n\nWe need invoices exported to CSV every month."
    assert first.raw["thread_id"] == "18f0a1b2c3d4e5f6"

    # Quoted earlier message removed from the reply
    assert reply.text == "Should the CSV include cancelled invoices?"
    assert reply.channel == "Invoice export"
    assert reply.author == "team@reqguard.dev"

    # HTML only (ISO-8859-1), nested multipart, attachment ignored, <blockquote> dropped
    assert html_mail.text == "Café menu\n\nThe menu must show prices in € and the café opening hours."
    assert html_mail.author == "Müller, Jens"


@respx.mock
def test_since_adds_after_filter():
    _, threads = mock_mailbox(respx.mock)
    with make_connector() as connector:
        connector.fetch(since=datetime(2026, 10, 22, tzinfo=timezone.utc))
    # One day margin before `since`
    assert threads.calls[0].request.url.params["q"] == "from:acme.example.com after:1792540800"


@respx.mock
def test_revoked_refresh_token_needs_new_auth():
    respx.post(TOKEN_URL).respond(400, json=api_json("gmail/token_invalid_grant.json"))
    with make_connector() as connector, pytest.raises(NeedsAuthError, match="Connect the Google account again"):
        connector.fetch()


@respx.mock
def test_wrong_client_secret():
    respx.post(TOKEN_URL).respond(401, json={"error": "invalid_client"})
    with make_connector() as connector, pytest.raises(ConnectorError, match="GOOGLE_CLIENT_SECRET"):
        connector.fetch()


# ---------- Voice notes ----------


def _b64(data: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _thread_with_audio(attachment_id: str = "ANGjdJ8-old") -> dict:
    return {
        "id": "t9",
        "messages": [
            {
                "id": "m9",
                "threadId": "t9",
                "internalDate": "1792656000000",
                "labelIds": ["INBOX"],
                "payload": {
                    "mimeType": "multipart/mixed",
                    "headers": [{"name": "From", "value": "Karim <karim@acme.example.com>"},
                                {"name": "Subject", "value": "Voice brief"}],
                    "parts": [
                        {"partId": "0", "mimeType": "text/plain", "filename": "",
                         "body": {"data": _b64(b"Please listen to the note.")}},
                        {"partId": "1", "mimeType": "audio/x-m4a", "filename": "brief.m4a",
                         "body": {"attachmentId": attachment_id, "size": 42000}},
                        {"partId": "2", "mimeType": "image/png", "filename": "logo.png",
                         "body": {"attachmentId": "IMG", "size": 10}},
                    ],
                },
            }
        ],
    }


@respx.mock
def test_audio_attachments_become_voice_messages():
    respx.post(TOKEN_URL).respond(json=api_json("gmail/token_refresh.json"))
    respx.get(f"{API}/threads").respond(json={"threads": [{"id": "t9"}]})
    respx.get(f"{API}/threads/t9").respond(json=_thread_with_audio())
    with make_connector() as connector:
        messages = connector.fetch()
    assert [(m.kind, m.external_id) for m in messages] == [("text", "m9"), ("voice", "m9:audio:1")]
    voice = messages[1].audio
    assert (voice.filename, voice.mime_type, voice.size) == ("brief.m4a", "audio/x-m4a", 42000)
    assert voice.ref == {"message_id": "m9", "attachment_id": "ANGjdJ8-old", "part_id": "1"}


@respx.mock
def test_download_audio_uses_attachments_get():
    token = respx.post(TOKEN_URL).respond(json=api_json("gmail/token_refresh.json"))
    route = respx.get(f"{API}/messages/m9/attachments/ANGjdJ8-old").respond(json={"data": _b64(b"fake-m4a")})
    attachment = AudioAttachment(
        file_id="1", filename="brief.m4a", mime_type="audio/mp4",
        ref={"message_id": "m9", "attachment_id": "ANGjdJ8-old", "part_id": "1"},
    )
    with make_connector() as connector:
        assert connector.download_audio(attachment) == b"fake-m4a"
    assert route.calls[0].request.headers["Authorization"].startswith("Bearer ")
    assert token.call_count == 1


@respx.mock
def test_download_audio_reads_the_message_again_when_the_attachment_id_changed():
    respx.post(TOKEN_URL).respond(json=api_json("gmail/token_refresh.json"))
    respx.get(f"{API}/messages/m9/attachments/ANGjdJ8-old").respond(400)
    respx.get(f"{API}/messages/m9").respond(json=_thread_with_audio("ANGjdJ8-new")["messages"][0])
    respx.get(f"{API}/messages/m9/attachments/ANGjdJ8-new").respond(json={"data": _b64(b"fresh")})
    attachment = AudioAttachment(
        file_id="1", filename="brief.m4a", mime_type="audio/mp4",
        ref={"message_id": "m9", "attachment_id": "ANGjdJ8-old", "part_id": "1"},
    )
    with make_connector() as connector:
        assert connector.download_audio(attachment) == b"fresh"
