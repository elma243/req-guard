"""Jira Cloud connector (REST API v3, email + API token).

Imports the issues of one project: each issue becomes one message (summary + description) and each
comment becomes one message. Jira v3 returns rich text as Atlassian Document Format (ADF);
`adf_to_text` turns it into plain text.

Voice notes: every audio attachment of an issue becomes a voice message (Jira keeps comment attachments on the
issue too). When a comment shows the file (an ADF media node with the file name), `raw["comment_id"]` links it to
that comment. The audio is downloaded from the attachment's content URL with the API token.
"""

import re
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from pydantic import BaseModel, field_validator

from app.connectors.base import AudioAttachment, NormalizedMessage, as_utc, audio_mime, voice_message
from app.connectors.http import MAX_PAGES, ApiConnector, ConnectorError, download, json_body, send

SOURCE = "jira"
SERVICE = "Jira"
AUTH_HINT = "Check JIRA_BASE_URL, JIRA_EMAIL and JIRA_API_TOKEN in backend/.env."
PAGE_SIZE = 100
# JQL dates have minute precision and use the Jira user's time zone, so look back a bit further
SINCE_MARGIN = timedelta(days=1)

# ---------------------------------------------------------------------------
# Atlassian Document Format → plain text
# ---------------------------------------------------------------------------

_LISTS = {"bulletList", "orderedList"}
_SKIP = {"rule", "media", "mediaSingle", "mediaGroup", "mediaInline"}


def _inline(nodes: list[dict] | None) -> str:
    parts: list[str] = []
    for node in nodes or []:
        kind = node.get("type")
        attrs = node.get("attrs") or {}
        if kind == "text":
            parts.append(node.get("text", ""))
        elif kind == "hardBreak":
            parts.append("\n")
        elif kind in ("mention", "emoji", "status"):
            parts.append(attrs.get("text") or attrs.get("shortName") or "")
        elif kind == "inlineCard":
            parts.append(attrs.get("url", ""))
        elif kind == "date":
            try:
                day = datetime.fromtimestamp(int(attrs["timestamp"]) / 1000, tz=timezone.utc)
                parts.append(day.strftime("%Y-%m-%d"))
            except (KeyError, ValueError, TypeError):
                pass
        elif kind not in _SKIP:
            parts.append(_inline(node.get("content")))
    return "".join(parts)


def _blocks(nodes: list[dict] | None, depth: int = 0) -> list[str]:
    out: list[str] = []
    for node in nodes or []:
        kind = node.get("type")
        content = node.get("content")
        if kind in ("paragraph", "heading", "codeBlock"):
            out.append(_inline(content))
        elif kind in _LISTS:
            start = (node.get("attrs") or {}).get("order", 1)
            lines: list[str] = []
            for i, item in enumerate(content or []):
                marker = f"{start + i}." if kind == "orderedList" else "-"
                first, *rest = _blocks(item.get("content"), depth + 1) or [""]
                lines.append(f"{'  ' * depth}{marker} {first}")
                lines.extend(rest)  # nested lists are already indented
            out.append("\n".join(lines))
        elif kind == "table":
            rows = []
            for row in content or []:
                cells = [" ".join(_blocks(cell.get("content"), depth)) for cell in row.get("content") or []]
                rows.append(" | ".join(cells))
            out.append("\n".join(rows))
        elif kind in _SKIP:
            continue
        elif kind in ("text", "hardBreak", "mention", "emoji", "status", "inlineCard", "date"):
            out.append(_inline([node]))
        else:  # doc, blockquote, panel, expand, unknown containers
            out.extend(_blocks(content, depth))
    return [block for block in out if block.strip()]


def adf_to_text(value: Any) -> str:
    """Plain text of an ADF document. Plain strings (older API versions) are returned as they are."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, dict):
        return ""
    text = "\n\n".join(_blocks([value]))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# ---------------------------------------------------------------------------
# Connector
# ---------------------------------------------------------------------------


class JiraSettings(BaseModel):
    project_key: str

    @field_validator("project_key")
    @classmethod
    def check_key(cls, value: str) -> str:
        value = value.strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,29}", value):
            raise ValueError("Jira project key looks like ACME or WEB2 (letters, digits, underscore)")
        return value


def _jira_date(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return as_utc(datetime.strptime(value, fmt))
        except ValueError:
            continue
    return None


def media_names(value: Any) -> set[str]:
    """File names shown by ADF media nodes (their `alt`) anywhere in a document."""
    names: set[str] = set()
    if isinstance(value, dict):
        if value.get("type") in ("media", "mediaInline"):
            alt = (value.get("attrs") or {}).get("alt")
            if alt:
                names.add(str(alt))
        for child in value.get("content") or []:
            names |= media_names(child)
    return names


def _person(value: dict | None) -> str:
    value = value or {}
    return str(value.get("displayName") or value.get("emailAddress") or value.get("accountId") or "")


class JiraConnector(ApiConnector):
    source = SOURCE
    label = "Jira"
    required_env = ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN")
    Settings = JiraSettings

    def __init__(
        self,
        settings: JiraSettings,
        *,
        base_url: str,
        email: str,
        api_token: str,
        client: httpx.Client | None = None,
        state: dict | None = None,
    ):
        super().__init__(client, state)
        self.settings = settings
        self.base_url = base_url.rstrip("/")
        self.auth = httpx.BasicAuth(email, api_token)

    def _get(self, path: str, params: dict[str, Any]) -> Any:
        response = send(
            self.client,
            "GET",
            f"{self.base_url}{path}",
            service=SERVICE,
            auth_hint=AUTH_HINT,
            params=params,
            auth=self.auth,
            headers={"Accept": "application/json"},
        )
        return json_body(response, SERVICE)

    def _issues(self, since: datetime | None) -> list[dict]:
        jql = f'project = "{self.settings.project_key}"'
        if since is not None:
            jql += f' AND updated >= "{(since - SINCE_MARGIN).strftime("%Y/%m/%d %H:%M")}"'
        jql += " ORDER BY created ASC"
        params: dict[str, Any] = {
            "jql": jql,
            "fields": "summary,description,reporter,creator,created,updated,attachment",
            "maxResults": PAGE_SIZE,
        }
        issues: list[dict] = []
        for _ in range(MAX_PAGES):
            data = self._get("/rest/api/3/search/jql", params)
            issues.extend(data.get("issues") or [])
            token = data.get("nextPageToken")
            if data.get("isLast", True) or not token:
                break
            params["nextPageToken"] = token
        return issues

    def _comments(self, issue_key: str) -> list[dict]:
        comments: list[dict] = []
        start = 0
        for _ in range(MAX_PAGES):
            data = self._get(f"/rest/api/3/issue/{issue_key}/comment", {"startAt": start, "maxResults": PAGE_SIZE})
            page = data.get("comments") or []
            comments.extend(page)
            start += len(page)
            if not page or start >= int(data.get("total", 0)):
                break
        return comments

    def _voice_messages(
        self, issue: dict, key: str, issue_created: datetime, comments: list[dict]
    ) -> list[NormalizedMessage]:
        """One voice message per audio attachment, linked to the comment that shows it, if any."""
        found: list[NormalizedMessage] = []
        shown_in = {name: c for c in comments for name in media_names(c.get("body"))}
        for item in (issue.get("fields") or {}).get("attachment") or []:
            filename = str(item.get("filename") or "")
            mime = audio_mime(filename, item.get("mimeType"))
            if mime is None or not item.get("id"):
                continue
            comment = shown_in.get(filename)
            attachment = AudioAttachment(
                file_id=str(item["id"]),
                filename=filename,
                mime_type=mime,
                size=item.get("size") if isinstance(item.get("size"), int) else None,
                url=item.get("content"),
            )
            raw: dict[str, Any] = {"type": "attachment", "issue_key": key, "attachment": item}
            if comment is not None:
                raw["comment_id"] = comment.get("id")
            found.append(
                voice_message(
                    source=SOURCE,
                    # Always the issue: the id stays the same if a comment shows the file later
                    parent_id=f"issue:{issue['id']}",
                    channel=key,
                    author=_person(item.get("author") or (comment or {}).get("author")),
                    sent_at=_jira_date(item.get("created")) or issue_created,
                    attachment=attachment,
                    raw=raw,
                )
            )
        return found

    def download_audio(self, attachment: AudioAttachment) -> bytes:
        """The attachment's content URL with the API token (Jira redirects to its media service)."""
        if attachment.data is not None:
            return attachment.data
        url = attachment.url or f"{self.base_url}/rest/api/3/attachment/content/{attachment.file_id}"
        return download(self.client, url, service=SERVICE, auth_hint=AUTH_HINT, auth=self.auth)

    def fetch(self, since: datetime | None = None) -> list[NormalizedMessage]:
        messages: list[NormalizedMessage] = []
        for issue in self._issues(since):
            key = str(issue.get("key") or issue.get("id"))
            fields = issue.get("fields") or {}
            created = _jira_date(fields.get("created"))
            if created is None:
                raise ConnectorError(f"Jira issue {key} has no valid creation date.")
            summary = " ".join(str(fields.get("summary") or "").split())
            description = adf_to_text(fields.get("description"))
            text = f"{summary}\n\n{description}".strip()
            if text:
                messages.append(
                    NormalizedMessage(
                        source=SOURCE,
                        external_id=f"issue:{issue['id']}",
                        channel=key,
                        author=_person(fields.get("reporter") or fields.get("creator")),
                        sent_at=created,
                        text=text,
                        raw={"type": "issue", "key": key, "issue": issue},
                    )
                )
            comments = self._comments(key)
            messages.extend(self._voice_messages(issue, key, created, comments))
            for comment in comments:
                body = adf_to_text(comment.get("body"))
                sent_at = _jira_date(comment.get("created"))
                if not body or sent_at is None:
                    continue
                messages.append(
                    NormalizedMessage(
                        source=SOURCE,
                        external_id=f"comment:{comment['id']}",
                        channel=key,
                        author=_person(comment.get("author")),
                        sent_at=sent_at,
                        text=body,
                        raw={"type": "comment", "issue_key": key, "comment": comment},
                    )
                )
        return sorted(messages, key=lambda m: m.sent_at)
