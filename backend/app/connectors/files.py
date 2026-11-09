"""Parsers for uploaded files (no network): .eml emails (with their audio attachments as voice notes) and
.txt/.md meeting notes.

Also decides which parser an uploaded file needs (`detect_type`, `connector_for_upload`).
"""

import hashlib
import json
import re
from datetime import datetime, timezone
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import PurePath

from app.connectors.base import (
    AudioAttachment,
    FileConnector,
    NormalizedMessage,
    OccurrenceCounter,
    ParseResult,
    as_utc,
    audio_mime,
    decode_text,
    stable_id,
    voice_message,
)
from app.connectors.archive import ArchiveError, open_zip, read_text_member
from app.connectors.messenger_export import (
    MessengerExportConnector,
    MessengerZipConnector,
    is_messenger_json,
    message_files,
)
from app.connectors.whatsapp_export import WhatsAppExportConnector, WhatsAppZipConnector, find_chat, is_whatsapp_text

# ---------------------------------------------------------------------------
# Email (.eml)
# ---------------------------------------------------------------------------

EMAIL_SOURCE = "email"

_REPLY_PREFIX_RE = re.compile(r"^\s*(?:(?:re|fwd?|aw)\s*:\s*)+", re.IGNORECASE)
_ON_WROTE_RE = re.compile(r"^On .{5,200} wrote:\s*$", re.IGNORECASE)
_ORIGINAL_RE = re.compile(r"^-{2,}\s*Original Message\s*-{2,}$", re.IGNORECASE)
_SENT_FROM_RE = re.compile(r"^Sent from my \w+", re.IGNORECASE)
_SIGN_OFF_RE = re.compile(
    r"^(?:(?:best|kind|warm|many)\s+)?(?:regards|wishes|thanks|thank you|cheers|sincerely|best)\b[\s,.!]*$",
    re.IGNORECASE,
)
# A sign-off only counts as the start of a signature near the end of the email
_SIGNATURE_WINDOW = 6


class _HtmlToText(HTMLParser):
    """Very small HTML → text converter. Quoted replies inside <blockquote> are dropped."""

    BLOCK_TAGS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table", "ul", "ol"}
    SKIP_TAGS = {"script", "style", "head", "blockquote"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP_TAGS:
            self.skip_depth += 1
        elif tag in self.BLOCK_TAGS:
            self.parts.append("\n")
        if tag == "li" and not self.skip_depth:
            self.parts.append("- ")

    def handle_endtag(self, tag):
        if tag in self.SKIP_TAGS:
            self.skip_depth = max(0, self.skip_depth - 1)
        elif tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip_depth:
            self.parts.append(re.sub(r"\s+", " ", data))


def html_to_text(html: str) -> str:
    parser = _HtmlToText()
    parser.feed(html)
    lines = [line.strip() for line in "".join(parser.parts).splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def thread_subject(subject: str) -> str:
    """The subject without "Re:" / "Fwd:" prefixes, used as the conversation name."""
    return _REPLY_PREFIX_RE.sub("", " ".join(subject.split()))


def _is_outlook_header(lines: list[str], i: int) -> bool:
    """'From: ...' followed within a few lines by 'Sent:' or 'Date:' starts a quoted Outlook reply."""
    if not lines[i].strip().startswith("From:"):
        return False
    return any(re.match(r"^(Sent|Date):", line.strip()) for line in lines[i + 1 : i + 4])


def clean_email_body(body: str) -> str:
    """Remove quoted replies and the signature, keeping only what the sender wrote now."""
    lines = body.replace("\r\n", "\n").split("\n")
    kept: list[str] = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        two_lines = f"{stripped} {lines[i + 1].strip()}" if i + 1 < len(lines) else stripped
        if (
            _ON_WROTE_RE.match(stripped)
            or (stripped.startswith("On ") and _ON_WROTE_RE.match(two_lines))
            or _ORIGINAL_RE.match(stripped)
            or (re.fullmatch(r"_{10,}", stripped) and i + 1 < len(lines) and _is_outlook_header(lines, i + 1))
            or _is_outlook_header(lines, i)
        ):
            break  # everything below is the earlier conversation
        if stripped.startswith(">"):
            continue
        if line.rstrip() == "--" or _SENT_FROM_RE.match(stripped):
            break  # standard signature delimiter / mobile footer
        kept.append(line.rstrip())

    # A sign-off ("Best regards,") close to the end starts the signature
    non_empty = [i for i, line in enumerate(kept) if line.strip()]
    for i in non_empty[-_SIGNATURE_WINDOW:]:
        if _SIGN_OFF_RE.match(kept[i].strip()) and any(line.strip() for line in kept[:i]):
            kept = kept[:i]
            break

    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()


def _email_body(msg: EmailMessage) -> str:
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        content = part.get_content()
    except (LookupError, UnicodeDecodeError):
        content = part.get_payload(decode=True).decode("utf-8", errors="replace")
    return html_to_text(content) if part.get_content_subtype() == "html" else content


def _email_date(msg: EmailMessage, fallback: datetime) -> datetime:
    value = msg.get("Date")
    if value:
        try:
            return as_utc(parsedate_to_datetime(str(value)))
        except (TypeError, ValueError):
            pass
    return fallback


def _email_author(msg: EmailMessage) -> str:
    header = msg.get("From")
    addresses = getattr(header, "addresses", ()) if header is not None else ()
    if addresses:
        return addresses[0].display_name or addresses[0].addr_spec
    return str(header or "").strip()


def email_audio(msg: EmailMessage) -> list[AudioAttachment]:
    """Audio attachments of an email, with their bytes."""
    found: list[AudioAttachment] = []
    for index, part in enumerate(msg.iter_attachments()):
        name = part.get_filename() or f"attachment-{index + 1}"
        mime = audio_mime(name, part.get_content_type())
        if mime is None:
            continue
        try:
            data = part.get_payload(decode=True) or b""
        except (LookupError, ValueError):
            continue
        found.append(
            AudioAttachment(file_id=f"{index + 1}:{name}", filename=name, mime_type=mime, size=len(data), data=data)
        )
    return found


def parse_eml(data: bytes, filename: str, now: datetime | None = None) -> ParseResult:
    msg = BytesParser(policy=policy.default).parsebytes(data)
    subject = " ".join(str(msg.get("Subject") or "").split())
    original_body = _email_body(msg)
    body = clean_email_body(original_body)
    audio = email_audio(msg)
    result = ParseResult()
    if not body and not subject and not audio:
        result.skipped = 1
        return result

    author = _email_author(msg)
    sent_at = _email_date(msg, now or datetime.now(timezone.utc))
    message_id = str(msg.get("Message-ID") or "").strip().strip("<>")
    text = f"{subject}\n\n{body}".strip() if subject else body
    external_id = message_id or stable_id(EMAIL_SOURCE, author, sent_at.isoformat(), subject, body)
    channel = thread_subject(subject) or "(no subject)"
    raw = {
        "filename": filename,
        "message_id": message_id,
        "from": str(msg.get("From") or ""),
        "to": str(msg.get("To") or ""),
        "cc": str(msg.get("Cc") or ""),
        "date": str(msg.get("Date") or ""),
        "subject": subject,
        "in_reply_to": str(msg.get("In-Reply-To") or ""),
        "body": original_body,
    }
    if text:
        result.messages.append(
            NormalizedMessage(
                source=EMAIL_SOURCE, external_id=external_id, channel=channel, author=author, sent_at=sent_at,
                text=text, raw=raw,
            )
        )
    for attachment in audio:
        result.messages.append(
            voice_message(
                source=EMAIL_SOURCE, parent_id=external_id, channel=channel, author=author, sent_at=sent_at,
                attachment=attachment, raw=raw,
            )
        )
    return result


class EmailFileConnector(FileConnector):
    source = EMAIL_SOURCE

    def parse(self) -> ParseResult:
        return parse_eml(self.data, self.filename)


# ---------------------------------------------------------------------------
# Meeting notes (.txt / .md)
# ---------------------------------------------------------------------------

NOTES_SOURCE = "meeting_notes"

_DATE_LINE_RE = re.compile(r"^\s*(?:\*\*|__)?date(?:\*\*|__)?\s*:\s*(?:\*\*|__)?\s*(?P<value>.+)$", re.IGNORECASE)
_ISO_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_LONG_DATE_RE = re.compile(r"\b(\d{1,2} [A-Z][a-z]+ \d{4}|[A-Z][a-z]+ \d{1,2},? \d{4})\b")
_DATE_FORMATS = ("%B %d, %Y", "%B %d %Y", "%b %d, %Y", "%b %d %Y", "%d %B %Y", "%d %b %Y", "%d/%m/%Y")
_METADATA_LINE_RE = re.compile(
    r"^\s*(?:\*\*|__)?(?:date|time|attendees|participants|present|location|meeting|title)(?:\*\*|__)?\s*:",
    re.IGNORECASE,
)
_RULE_RE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s*")
# How many lines at the top of the notes are searched for the meeting date
_DATE_SEARCH_LINES = 10


def _parse_date_text(value: str) -> datetime | None:
    value = value.strip().strip("*_ ")
    iso = _ISO_DATE_RE.search(value)
    if iso:
        return datetime.fromisoformat(iso.group(1))
    long = _LONG_DATE_RE.search(value)
    candidates = [long.group(1)] if long else []
    candidates.append(value)
    for candidate in candidates:
        for fmt in _DATE_FORMATS:
            try:
                return datetime.strptime(candidate, fmt)
            except ValueError:
                continue
    return None


def find_notes_date(text: str) -> datetime | None:
    """The meeting date: a 'Date:' line, else the first ISO or written-out date near the top."""
    top = text.splitlines()[:_DATE_SEARCH_LINES]
    for line in top:
        match = _DATE_LINE_RE.match(line)
        if match and (found := _parse_date_text(match["value"])):
            return as_utc(found)
    for line in top:
        if _ISO_DATE_RE.search(line) or _LONG_DATE_RE.search(line):
            if found := _parse_date_text(line):
                return as_utc(found)
    return None


def parse_meeting_notes(text: str, filename: str, now: datetime | None = None) -> ParseResult:
    channel = PurePath(filename).stem
    sent_at = find_notes_date(text) or now or datetime.now(timezone.utc)
    result = ParseResult()
    counter = OccurrenceCounter()

    paragraphs = re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip())
    for index, paragraph in enumerate(paragraphs):
        lines = [line.rstrip() for line in paragraph.split("\n") if line.strip()]
        if not lines:
            continue
        if all(_HEADING_RE.match(line) or _RULE_RE.match(line) for line in lines) or all(
            _METADATA_LINE_RE.match(line) for line in lines
        ):
            result.skipped += 1  # heading, horizontal rule or "Date: / Attendees:" block
            continue

        body = "\n".join(_HEADING_RE.sub("", line) for line in lines).strip()
        occurrence = counter.next(body)
        result.messages.append(
            NormalizedMessage(
                source=NOTES_SOURCE,
                external_id=stable_id(NOTES_SOURCE, channel, body, occurrence),
                channel=channel,
                author="",
                sent_at=sent_at,
                text=body,
                raw={"filename": filename, "paragraph": index + 1, "text": paragraph},
            )
        )
    return result


class MeetingNotesConnector(FileConnector):
    source = NOTES_SOURCE

    def parse(self) -> ParseResult:
        return parse_meeting_notes(decode_text(self.data), self.filename)


# ---------------------------------------------------------------------------
# Voice note files (manual upload on the Sources page)
# ---------------------------------------------------------------------------

VOICE_SOURCE = "voice_upload"
# Audio files the Sources page accepts
VOICE_EXTENSIONS = (".mp3", ".m4a", ".ogg", ".opus", ".wav", ".webm")


class VoiceFileConnector(FileConnector):
    """One uploaded audio file becomes one voice message. Its id is the hash of the audio, so uploading the same
    file again is a duplicate."""

    source = VOICE_SOURCE

    def __init__(self, data: bytes, filename: str, now: datetime | None = None):
        super().__init__(data, filename)
        self.now = now

    def parse(self) -> ParseResult:
        digest = hashlib.sha1(self.data).hexdigest()
        attachment = AudioAttachment(
            file_id="file",
            filename=self.filename,
            mime_type=audio_mime(self.filename) or "audio/mpeg",
            size=len(self.data),
            data=self.data,
        )
        message = voice_message(
            source=VOICE_SOURCE,
            parent_id=digest,
            channel="Voice notes",
            author="",
            sent_at=self.now or datetime.now(timezone.utc),
            attachment=attachment,
            raw={"filename": self.filename},
        )
        return ParseResult(messages=[message])


# ---------------------------------------------------------------------------
# Upload type detection
# ---------------------------------------------------------------------------

CONNECTORS: dict[str, type[FileConnector]] = {
    "whatsapp": WhatsAppExportConnector,
    "whatsapp_zip": WhatsAppZipConnector,
    "messenger": MessengerExportConnector,
    "messenger_zip": MessengerZipConnector,
    EMAIL_SOURCE: EmailFileConnector,
    NOTES_SOURCE: MeetingNotesConnector,
    VOICE_SOURCE: VoiceFileConnector,
}
SUPPORTED_EXTENSIONS = (".txt", ".md", ".eml", ".json", ".zip", *VOICE_EXTENSIONS)


def zip_kind(data: bytes) -> str | None:
    """whatsapp_zip / messenger_zip from the files inside, or None."""
    try:
        archive = open_zip(data)
        if find_chat(archive) is not None:
            return "whatsapp_zip"
        for info in message_files(archive):
            if is_messenger_json(json.loads(read_text_member(archive, info).decode("utf-8-sig"))):
                return "messenger_zip"
    except (ArchiveError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return None


def detect_type(filename: str, data: bytes) -> str | None:
    """Which parser to use, from the extension and (for .txt/.json) the content. None = unsupported."""
    ext = PurePath(filename).suffix.lower()
    if ext == ".eml":
        return EMAIL_SOURCE
    if ext == ".zip":
        return zip_kind(data)
    if ext in VOICE_EXTENSIONS:
        return VOICE_SOURCE
    if ext == ".json":
        try:
            return "messenger" if is_messenger_json(json.loads(data.decode("utf-8-sig"))) else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
    if ext == ".txt":
        return "whatsapp" if is_whatsapp_text(decode_text(data)) else NOTES_SOURCE
    if ext == ".md":
        return NOTES_SOURCE
    return None


def connector_for_upload(filename: str, data: bytes) -> FileConnector | None:
    kind = detect_type(filename, data)
    return CONNECTORS[kind](data, filename) if kind else None
