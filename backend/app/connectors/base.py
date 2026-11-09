"""Shared connector types: the normalised message, audio attachments and the Connector interface.

Every source (chat export, file, or API) turns its data into NormalizedMessage objects. Nothing here touches the
database.

Voice notes: a connector reports each audio file as its own voice message (`voice_message()`), with an
AudioAttachment that says how to get the audio (inline bytes for uploads, or an id/URL that the connector's
`download_audio()` fetches). Transcription is not done by connectors: app/ai/speech.py does it for every source.
"""

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import PurePath
from typing import Any

from pydantic import BaseModel, Field

KIND_TEXT = "text"
KIND_VOICE = "voice"

# Largest audio file that is downloaded and transcribed (Groq's limit for one transcription request)
MAX_AUDIO_BYTES = 25 * 1024 * 1024
TOO_LARGE_NOTE = "Voice note is larger than 25 MB and was not transcribed."

# Audio file extensions and the MIME type sent to the transcription API
AUDIO_EXTENSIONS = {
    ".mp3": "audio/mpeg",
    ".mpga": "audio/mpeg",
    ".mpeg": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".mp4": "audio/mp4",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".opus": "audio/ogg",
    ".wav": "audio/wav",
    ".webm": "audio/webm",
    ".flac": "audio/flac",
}


def audio_mime(filename: str, mime: str | None = "") -> str | None:
    """The audio MIME type of a file, or None when it is not audio. An audio/* type wins over the extension;
    a generic type (application/octet-stream, video/mp4 for Messenger clips) falls back to the extension."""
    mime = (mime or "").split(";")[0].strip().lower()
    if mime.startswith("audio/"):
        return mime
    return AUDIO_EXTENSIONS.get(PurePath(filename or "").suffix.lower())


class AudioAttachment(BaseModel):
    """One audio file attached to a message."""

    # Id of the file at the source (Telegram file_id, Slack file id, Gmail attachmentId, Jira attachment id,
    # Discord attachment id, file name inside an uploaded ZIP...)
    file_id: str
    filename: str
    mime_type: str
    size: int | None = None
    duration: float | None = None
    # Download address when the source gives one (never one that contains a token)
    url: str | None = None
    # Whatever else the connector needs to download it again later (e.g. the Gmail message id)
    ref: dict[str, Any] = Field(default_factory=dict)
    # Inline audio (uploads); never stored in `raw`
    data: bytes | None = Field(default=None, exclude=True, repr=False)
    # Set when the connector already knows the file cannot be transcribed (e.g. missing from the upload)
    skip_reason: str | None = None

    def metadata(self) -> dict[str, Any]:
        """What is kept in Message.raw["voice"]."""
        return self.model_dump(exclude={"data", "skip_reason"})


class NormalizedMessage(BaseModel):
    """One message in the unified format (CLAUDE.md rule 1). Voice messages have kind "voice", an `audio`
    attachment and empty text until they are transcribed."""

    source: str
    external_id: str
    channel: str = ""
    author: str = ""
    sent_at: datetime
    text: str
    raw: dict[str, Any] = Field(default_factory=dict)
    kind: str = KIND_TEXT
    audio: AudioAttachment | None = None


def voice_message(
    *,
    source: str,
    parent_id: str,
    channel: str,
    author: str,
    sent_at: datetime,
    attachment: AudioAttachment,
    raw: dict[str, Any],
) -> NormalizedMessage:
    """A voice message for one audio file of the source message `parent_id`.

    Each audio file is its own message, so a message with text and audio keeps its text message unchanged and
    re-importing gives the same id.
    """
    return NormalizedMessage(
        source=source,
        external_id=f"{parent_id}:audio:{attachment.file_id}",
        channel=channel,
        author=author,
        sent_at=sent_at,
        text="",
        raw={**raw, "parent_external_id": parent_id},
        kind=KIND_VOICE,
        audio=attachment,
    )


@dataclass
class ParseResult:
    messages: list[NormalizedMessage] = field(default_factory=list)
    # Lines or records that were recognised but not imported (system lines, media, empty...)
    skipped: int = 0


class Connector(ABC):
    """Anything that can produce messages. `since` limits the result to newer messages."""

    source: str

    @abstractmethod
    def fetch(self, since: datetime | None = None) -> list[NormalizedMessage]: ...

    def download_audio(self, attachment: AudioAttachment) -> bytes:
        """The audio of an attachment this connector returned. Uploads carry it inline; API connectors download it
        (and raise ConnectorError with a clear message when they cannot)."""
        if attachment.data is not None:
            return attachment.data
        raise NotImplementedError("This source cannot download audio again. Upload the file again.")


class FileConnector(Connector):
    """A connector over one uploaded file. Subclasses only implement `parse()`."""

    def __init__(self, data: bytes, filename: str):
        self.data = data
        self.filename = filename
        self.skipped = 0

    @abstractmethod
    def parse(self) -> ParseResult: ...

    def fetch(self, since: datetime | None = None) -> list[NormalizedMessage]:
        result = self.parse()
        self.skipped = result.skipped
        if since is None:
            return result.messages
        return [m for m in result.messages if m.sent_at > since]


def decode_text(data: bytes) -> str:
    """Decode an uploaded text file: UTF-8 (with or without BOM), else Windows-1252."""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def stable_id(*parts: object) -> str:
    """Deterministic id from the given parts, so re-importing the same message gives the same id."""
    joined = "\x1f".join(str(p) for p in parts)
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()


class OccurrenceCounter:
    """Numbers identical keys 0, 1, 2... so two equal messages in one file get different ids."""

    def __init__(self) -> None:
        self._seen: dict[tuple, int] = {}

    def next(self, *key: object) -> int:
        n = self._seen.get(key, 0)
        self._seen[key] = n + 1
        return n


def as_utc(dt: datetime) -> datetime:
    """Naive datetimes (exports without a timezone) are taken as UTC."""
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
