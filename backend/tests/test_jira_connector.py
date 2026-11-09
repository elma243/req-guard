"""Jira connector: ADF → text, issues + comments, pagination, errors. All HTTP is mocked."""

from datetime import datetime, timezone

import httpx
import pytest
import respx

from app.connectors.base import AudioAttachment
from app.connectors.http import ConnectorError
from app.connectors.jira import JiraConnector, JiraSettings, adf_to_text
from tests.conftest import api_json

BASE = "https://acme-demo.atlassian.net"


def make_connector(key: str = "ACME") -> JiraConnector:
    return JiraConnector(JiraSettings(project_key=key), base_url=BASE + "/", email="me@x.com", api_token="tok")


def mock_project(router: respx.Router):
    search = router.get(f"{BASE}/rest/api/3/search/jql")
    search.side_effect = [
        httpx.Response(200, json=api_json("jira/search_page1.json")),
        httpx.Response(200, json=api_json("jira/search_page2.json")),
    ]
    router.get(f"{BASE}/rest/api/3/issue/ACME-1/comment").respond(json=api_json("jira/comments_ACME-1.json"))
    router.get(f"{BASE}/rest/api/3/issue/ACME-2/comment").respond(json=api_json("jira/comments_ACME-2.json"))
    return search


# ---------- ADF ----------


def test_adf_paragraphs_marks_and_lists():
    description = api_json("jira/search_page1.json")["issues"][0]["fields"]["description"]
    assert adf_to_text(description) == (
        "Users must be able to reset their password by email.\n\n"
        "- The link expires after 30 minutes\n"
        "- It should be fast"
    )


def test_adf_nested_ordered_list_heading_table_and_inline_nodes():
    doc = {
        "type": "doc",
        "content": [
            {"type": "heading", "attrs": {"level": 2}, "content": [{"type": "text", "text": "Scope"}]},
            {
                "type": "orderedList",
                "attrs": {"order": 3},
                "content": [
                    {
                        "type": "listItem",
                        "content": [
                            {"type": "paragraph", "content": [{"type": "text", "text": "Login"}]},
                            {
                                "type": "bulletList",
                                "content": [
                                    {
                                        "type": "listItem",
                                        "content": [{"type": "paragraph", "content": [{"type": "text", "text": "SSO"}]}],
                                    }
                                ],
                            },
                        ],
                    }
                ],
            },
            {"type": "rule"},
            {"type": "mediaSingle", "content": [{"type": "media", "attrs": {"id": "x"}}]},
            {
                "type": "table",
                "content": [
                    {
                        "type": "tableRow",
                        "content": [
                            {"type": "tableHeader", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Role"}]}]},
                            {"type": "tableHeader", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Limit"}]}]},
                        ],
                    },
                    {
                        "type": "tableRow",
                        "content": [
                            {"type": "tableCell", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Admin"}]}]},
                            {"type": "tableCell", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "10"}]}]},
                        ],
                    },
                ],
            },
            {
                "type": "panel",
                "content": [
                    {
                        "type": "paragraph",
                        "content": [
                            {"type": "text", "text": "See "},
                            {"type": "inlineCard", "attrs": {"url": "https://example.com/spec"}},
                            {"type": "text", "text": " by "},
                            {"type": "date", "attrs": {"timestamp": "1792800000000"}},
                            {"type": "emoji", "attrs": {"shortName": ":smile:", "text": "😄"}},
                        ],
                    }
                ],
            },
        ],
    }
    assert adf_to_text(doc) == (
        "Scope\n\n"
        "3. Login\n"
        "  - SSO\n\n"
        "Role | Limit\n"
        "Admin | 10\n\n"
        "See https://example.com/spec by 2026-10-24😄"
    )


def test_adf_plain_string_and_empty_values():
    assert adf_to_text("  plain text  ") == "plain text"
    assert adf_to_text(None) == ""
    assert adf_to_text({"type": "doc", "content": []}) == ""


# ---------- Settings ----------


def test_project_key_is_normalised_and_checked():
    assert JiraSettings(project_key=" acme ").project_key == "ACME"
    with pytest.raises(ValueError):
        JiraSettings(project_key="ACME\" OR project = OTHER")


# ---------- Fetch ----------


@respx.mock
def test_fetch_issues_and_comments_with_pagination():
    search = mock_project(respx.mock)
    with make_connector() as connector:
        messages = connector.fetch()

    assert [m.external_id for m in messages] == ["issue:10001", "comment:20001", "comment:20002", "issue:10002"]
    issue = messages[0]
    assert issue.source == "jira"
    assert issue.channel == "ACME-1"
    assert issue.author == "Sarah Client"
    assert issue.sent_at == datetime(2026, 10, 20, 9, 15, tzinfo=timezone.utc)
    assert issue.text.startswith("Password reset by email\n\nUsers must be able to reset")
    assert issue.raw["key"] == "ACME-1"

    assert messages[1].text == "@Sarah Client should admins also get a reset link?"
    assert messages[1].author == "Tom Manager"
    assert messages[2].text == "Yes, admins too.\nThanks!"
    # Issue without a description: only the summary; +0600 offset converted to UTC
    assert messages[3].text == "Export monthly report"
    assert messages[3].sent_at == datetime(2026, 10, 21, 2, 0, tzinfo=timezone.utc)

    first, second = (call.request for call in search.calls)
    assert first.url.params["jql"] == 'project = "ACME" ORDER BY created ASC'
    assert "nextPageToken" not in first.url.params
    assert second.url.params["nextPageToken"] == "page-2-token"
    assert first.headers["Authorization"].startswith("Basic ")


@respx.mock
def test_since_adds_updated_filter():
    search = mock_project(respx.mock)
    with make_connector() as connector:
        connector.fetch(since=datetime(2026, 10, 21, 12, 30, tzinfo=timezone.utc))
    jql = search.calls[0].request.url.params["jql"]
    assert jql == 'project = "ACME" AND updated >= "2026/10/20 12:30" ORDER BY created ASC'


@respx.mock
def test_comment_pagination():
    respx.get(f"{BASE}/rest/api/3/search/jql").respond(
        json={"issues": [api_json("jira/search_page1.json")["issues"][0]], "isLast": True}
    )
    page = api_json("jira/comments_ACME-1.json")
    comments = respx.get(f"{BASE}/rest/api/3/issue/ACME-1/comment")
    comments.side_effect = [
        httpx.Response(200, json={**page, "total": 2, "comments": page["comments"][:1]}),
        httpx.Response(200, json={**page, "startAt": 1, "total": 2, "comments": page["comments"][1:]}),
    ]
    with make_connector() as connector:
        messages = connector.fetch()
    assert len(messages) == 3
    assert comments.calls[1].request.url.params["startAt"] == "1"


@respx.mock
def test_wrong_token_gives_clear_error():
    respx.get(f"{BASE}/rest/api/3/search/jql").respond(401, json={"errorMessages": ["Unauthorized"]})
    with make_connector() as connector, pytest.raises(ConnectorError, match="JIRA_API_TOKEN"):
        connector.fetch()


@respx.mock
def test_unknown_project_gives_clear_error():
    respx.get(f"{BASE}/rest/api/3/search/jql").respond(
        400, json={"errorMessages": ["The value 'NOPE' does not exist for the field 'project'."]}
    )
    with make_connector("NOPE") as connector, pytest.raises(ConnectorError, match="HTTP 400"):
        connector.fetch()


# ---------- Voice notes ----------

CONTENT = f"{BASE}/rest/api/3/attachment/content/10001"


def _issue_with_audio() -> dict:
    return {
        "id": "20001",
        "key": "ACME-9",
        "fields": {
            "summary": "Checkout",
            "created": "2026-10-20T10:00:00.000+0600",
            "reporter": {"displayName": "Karim"},
            "attachment": [
                {"id": "10001", "filename": "checkout-note.m4a", "mimeType": "audio/x-m4a", "size": 5000,
                 "created": "2026-10-21T09:00:00.000+0600", "author": {"displayName": "Rina"}, "content": CONTENT},
                {"id": "10002", "filename": "brief.ogg", "mimeType": "application/octet-stream", "size": 7000,
                 "created": "2026-10-20T10:05:00.000+0600", "author": {"displayName": "Karim"},
                 "content": f"{BASE}/rest/api/3/attachment/content/10002"},
                {"id": "10003", "filename": "screen.png", "mimeType": "image/png"},
            ],
        },
    }


@respx.mock
def test_audio_attachments_on_issues_and_comments():
    respx.get(f"{BASE}/rest/api/3/search/jql").respond(json={"issues": [_issue_with_audio()], "isLast": True})
    comment = {
        "id": "30001",
        "author": {"displayName": "Rina"},
        "created": "2026-10-21T09:00:00.000+0600",
        "body": {"type": "doc", "version": 1, "content": [
            {"type": "mediaSingle", "content": [
                {"type": "media", "attrs": {"id": "uuid", "type": "file", "collection": "", "alt": "checkout-note.m4a"}}
            ]},
        ]},
    }
    respx.get(f"{BASE}/rest/api/3/issue/ACME-9/comment").respond(json={"comments": [comment], "total": 1})
    with make_connector() as connector:
        messages = connector.fetch()

    voice = [m for m in messages if m.kind == "voice"]
    assert [m.external_id for m in voice] == ["issue:20001:audio:10002", "issue:20001:audio:10001"]
    on_comment = voice[1]
    assert on_comment.raw["comment_id"] == "30001"
    assert (on_comment.author, on_comment.audio.url, on_comment.audio.mime_type) == ("Rina", CONTENT, "audio/x-m4a")
    assert "comment_id" not in voice[0].raw  # shown on no comment: belongs to the issue
    assert voice[0].audio.mime_type == "audio/ogg"


@respx.mock
def test_download_audio_with_the_api_token():
    route = respx.get(CONTENT).respond(302, headers={"Location": "https://api.media.atlassian.com/file/x/binary"})
    respx.get("https://api.media.atlassian.com/file/x/binary").respond(content=b"fake-m4a")
    attachment = AudioAttachment(file_id="10001", filename="n.m4a", mime_type="audio/mp4", url=CONTENT)
    with make_connector() as connector:
        assert connector.download_audio(attachment) == b"fake-m4a"
    assert route.calls[0].request.headers["Authorization"].startswith("Basic ")
