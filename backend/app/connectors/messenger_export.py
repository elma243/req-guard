"""Parser for Facebook Messenger "Download your information" JSON (message_1.json), no network.

Facebook writes UTF-8 text as if every byte were a Latin-1 character ("donâ\\u0080\\u0099t"
instead of "don't"); `fix_mojibake` undoes that. The newer export layout
(senderName / text / timestamp) is accepted too and is already correctly encoded.

Voice notes: records list their audio in `audio_files` (or `media` in the newer layout) by a path inside the
export folder. Uploaded as a ZIP of the folder, the audio is read from it; a JSON upload alone keeps the voice
messages, marked as not transcribed with the reason.
"""

import json
import re
import zipfile
from datetime import datetime, timezone
from typing import Any

from app.connectors.archive import ZipMedia, basename, members, open_zip, read_text_member
from app.connectors.base import (
    AudioAttachment,
    FileConnector,
    NormalizedMessage,
    OccurrenceCounter,
    ParseResult,
    audio_mime,
    stable_id,
    voice_message,
)

SOURCE = "messenger"

# Texts that Messenger generates itself; they are not something the client wrote
GENERATED_RE = re.compile(
    r"^(?:.+ )?(?:reacted .+ to (?:your|a) message|sent an attachment\.|"
    r"sent a (?:photo|video|sticker|GIF|link|voice message)\.|sent \d+ photos\.|unsent a message\.?|"
    r"started (?:a|the) (?:video )?call\.?|joined the (?:video )?call\.?|missed (?:a|your) (?:video )?call\.?)$",
    re.IGNORECASE,
)
SKIP_TYPES = {"Call", "Subscribe", "Unsubscribe"}
NOT_IN_UPLOAD = "The audio file is not in the upload. Upload the Messenger export folder as a ZIP to read voice notes."
NOT_IN_ZIP = "The audio file named in the export is not in the ZIP."
MESSAGE_FILE_RE = re.compile(r"^message_\d+\.json$", re.IGNORECASE)


def fix_mojibake(value: str) -> str:
    """Reverse Facebook's double encoding. Text that is already correct is returned unchanged."""
    try:
        return value.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value


def is_messenger_json(data: Any) -> bool:
    return isinstance(data, dict) and isinstance(data.get("messages"), list) and "participants" in data


def audio_uris(record: dict) -> list[str]:
    """Paths of the audio files of a record (`audio_files`, or audio items of the newer `media` list)."""
    uris: list[str] = []
    for key in ("audio_files", "media"):
        for item in record.get(key) or []:
            uri = item.get("uri") if isinstance(item, dict) else None
            if isinstance(uri, str) and audio_mime(uri):
                uris.append(uri)
    return uris


def _attachment(uri: str, media: ZipMedia | None) -> AudioAttachment:
    if media is not None:
        return media.attachment(uri, NOT_IN_ZIP)
    name = basename(uri)
    return AudioAttachment(file_id=name, filename=name, mime_type=audio_mime(name) or "audio/mp4",
                           skip_reason=NOT_IN_UPLOAD)


def parse_messenger(data: dict[str, Any], filename: str, media: ZipMedia | None = None) -> ParseResult:
    title = fix_mojibake(str(data.get("title") or data.get("threadName") or filename))
    thread = str(data.get("thread_path") or title)
    result = ParseResult()
    counter = OccurrenceCounter()
    rows: list[NormalizedMessage] = []

    for record in data["messages"]:
        if not isinstance(record, dict):
            result.skipped += 1
            continue
        author = fix_mojibake(str(record.get("sender_name") or record.get("senderName") or "")).strip()
        content = record.get("content", record.get("text"))
        timestamp_ms = record.get("timestamp_ms", record.get("timestamp"))
        text = fix_mojibake(content).strip() if isinstance(content, str) else ""
        uris = audio_uris(record)
        if (
            not isinstance(timestamp_ms, (int, float))
            or record.get("is_unsent")
            or record.get("type") in SKIP_TYPES
        ):
            result.skipped += 1
            continue
        if GENERATED_RE.match(text):
            text = ""  # "Karim sent a voice message." is Messenger's own text
        if not text and not uris:
            result.skipped += 1
            continue

        sent_at = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
        raw = {"filename": filename, "thread": thread, "record": record}
        if text:
            occurrence = counter.next(timestamp_ms, author, text)
            rows.append(
                NormalizedMessage(
                    source=SOURCE,
                    external_id=stable_id(SOURCE, thread, timestamp_ms, author, text, occurrence),
                    channel=title,
                    author=author,
                    sent_at=sent_at,
                    text=text,
                    raw=raw,
                )
            )
        for uri in uris:
            occurrence = counter.next(timestamp_ms, author, uri)
            rows.append(
                voice_message(
                    source=SOURCE,
                    parent_id=stable_id(SOURCE, thread, timestamp_ms, author, uri, occurrence),
                    channel=title,
                    author=author,
                    sent_at=sent_at,
                    attachment=_attachment(uri, media),
                    raw=raw,
                )
            )

    # The export lists the newest message first
    result.messages = sorted(rows, key=lambda m: m.sent_at)
    return result


def _load(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Not a valid JSON file: {exc}") from None


class MessengerExportConnector(FileConnector):
    source = SOURCE

    def parse(self) -> ParseResult:
        data = _load(self.data)
        if not is_messenger_json(data):
            raise ValueError("This JSON file is not a Messenger export (no 'participants' and 'messages').")
        return parse_messenger(data, self.filename)


def message_files(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    """The message_N.json files of a Messenger export ZIP."""
    return sorted(
        (info for info in members(archive) if MESSAGE_FILE_RE.match(basename(info.filename))),
        key=lambda info: info.filename,
    )


class MessengerZipConnector(FileConnector):
    """The Messenger export folder as a ZIP: message_N.json files and the audio they name."""

    source = SOURCE

    def parse(self) -> ParseResult:
        archive = open_zip(self.data)
        media = ZipMedia(archive)
        result = ParseResult()
        found = False
        for info in message_files(archive):
            data = _load(read_text_member(archive, info))
            if not is_messenger_json(data):
                continue
            found = True
            part = parse_messenger(data, basename(info.filename), media=media)
            result.messages.extend(part.messages)
            result.skipped += part.skipped
        if not found:
            raise ValueError("No Messenger conversation (message_1.json) was found in this ZIP.")
        result.messages.sort(key=lambda m: m.sent_at)
        return result
