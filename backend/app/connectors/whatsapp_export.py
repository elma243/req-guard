"""Parser for WhatsApp "Export chat" files (no network): the .txt, or the ZIP exported "with media".

Handles both layouts WhatsApp produces:
    Android: 12/03/24, 14:05 - Sarah Khan: text
    iOS:     [12/03/24, 2:05:12 PM] Sarah Khan: text
with 12h or 24h times, 2- or 4-digit years and messages that continue on the next lines.
System lines (encryption notice, "X added Y") and media placeholders are skipped.

Voice notes: a chat exported "with media" names its files, e.g.
    Android: 12/03/24, 14:05 - Sarah Khan: PTT-20240312-WA0003.opus (file attached)
    iOS:     [12/03/24, 2:05:12 PM] Sarah Khan: <attached: 00000012-AUDIO-2024-03-12-14-05-12.opus>
Each audio file named this way becomes a voice message with its audio read from the ZIP. When only the .txt was
uploaded (or the file is not in the ZIP) the voice message is kept, marked as not transcribed with the reason.
"""

import re
import zipfile
from datetime import datetime
from pathlib import PurePath

from app.connectors.archive import MAX_TEXT_BYTES, ZipMedia, basename, members, open_zip, read_text_member
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

SOURCE = "whatsapp"

_DATE = r"\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}"
_TIME = r"\d{1,2}[:.]\d{2}(?:[:.]\d{2})?(?: ?[AaPp]\.? ?[Mm]\.?)?"
# One regex for both layouts: "[date, time] rest" or "date, time - rest"
HEADER_RE = re.compile(
    rf"^(?:\[(?P<d1>{_DATE}),? (?P<t1>{_TIME})\] |(?P<d2>{_DATE}),? (?P<t2>{_TIME}) - )(?P<rest>.*)$"
)
# "Name: text"; names are short, so a long prefix before ": " is a system line, not an author
AUTHOR_RE = re.compile(r"^(?P<author>[^:\n]{1,60}): (?P<text>.*)$", re.DOTALL)
# iOS marks system messages and attachments with an invisible U+200E right after "Name: "
IOS_SYSTEM_MARK = ": ‎"

# Placeholders WhatsApp writes instead of the real content
SKIP_TEXT_RE = re.compile(
    r"^(?:<media omitted>|<attached: .+>|"
    r"(?:image|video|audio|sticker|gif|document|contact card) omitted|"
    r"this message was deleted\.?|you deleted this message\.?|missed (?:voice|video) call|null)$",
    re.IGNORECASE,
)
EDITED_RE = re.compile(r"\s*<This message was edited>$", re.IGNORECASE)
# A file sent in the chat, as written by an export "with media" (iOS / Android)
ATTACHED_RE = re.compile(
    r"^(?:<attached: (?P<ios>[^>]+)>|(?P<android>[^\s<>:/\\]+\.\w{2,5}) \(file attached\))$", re.IGNORECASE
)
NOT_IN_UPLOAD = 'The audio file is not in the upload. Export the chat "with media" and upload the ZIP.'
NOT_IN_ZIP = "The audio file named in the chat is not in the ZIP."

# Invisible direction marks that iOS exports put before lines and names
_INVISIBLE = dict.fromkeys(map(ord, "‎‏‪‫‬‭‮﻿"), None)
_SPACES = {0x202F: " ", 0x00A0: " "}


def clean_line(line: str) -> str:
    return line.translate(_INVISIBLE).translate(_SPACES).rstrip("\r")


def is_whatsapp_text(text: str, min_lines: int = 3) -> bool:
    """True if at least `min_lines` lines look like WhatsApp message headers."""
    hits = 0
    for line in text.splitlines():
        if HEADER_RE.match(clean_line(line)):
            hits += 1
            if hits >= min_lines:
                return True
    return False


def chat_name_from_filename(filename: str) -> str:
    stem = PurePath(filename).stem
    match = re.match(r"^WhatsApp Chat (?:with|-) (.+)$", stem, re.IGNORECASE)
    return (match.group(1) if match else stem).strip()


def _split_date(value: str) -> tuple[int, int, int]:
    a, b, y = (int(p) for p in re.split(r"[/.-]", value))
    return a, b, y


def _day_first(dates: list[str]) -> bool:
    """dd/mm unless the file proves mm/dd (a second number above 12 and no first number above 12)."""
    parts = [_split_date(d) for d in dates]
    if any(a > 12 for a, _, _ in parts):
        return True
    if any(b > 12 for _, b, _ in parts):
        return False
    return True


def _parse_datetime(date_str: str, time_str: str, day_first: bool) -> datetime:
    a, b, year = _split_date(date_str)
    day, month = (a, b) if day_first else (b, a)
    if year < 100:
        year += 2000

    time_str = time_str.strip()
    ampm = re.search(r"([AaPp])\.? ?[Mm]\.?$", time_str)
    clock = re.split(r"[:.]", re.sub(r" ?[AaPp]\.? ?[Mm]\.?$", "", time_str))
    hour, minute = int(clock[0]), int(clock[1])
    second = int(clock[2]) if len(clock) > 2 else 0
    if ampm:
        is_pm = ampm.group(1).lower() == "p"
        hour = hour % 12 + (12 if is_pm else 0)
    return datetime(year, month, day, hour, minute, second)


def _voice_attachment(name: str, media: ZipMedia | None) -> AudioAttachment:
    if media is not None:
        return media.attachment(name, NOT_IN_ZIP)
    return AudioAttachment(
        file_id=name, filename=name, mime_type=audio_mime(name) or "audio/ogg", skip_reason=NOT_IN_UPLOAD
    )


def parse_whatsapp(text: str, filename: str, media: ZipMedia | None = None) -> ParseResult:
    """Messages of an exported chat. `media` gives the files of a ZIP export (voice notes)."""
    channel = chat_name_from_filename(filename)

    # First pass: group lines into entries (a header line plus its continuation lines)
    entries: list[dict] = []
    for number, original in enumerate(text.splitlines(), start=1):
        line = clean_line(original)
        match = HEADER_RE.match(line)
        if match:
            entries.append(
                {
                    "date": match["d1"] or match["d2"],
                    "time": match["t1"] or match["t2"],
                    "rest": [match["rest"]],
                    "line": number,
                    "lines": [line],
                    "system": IOS_SYSTEM_MARK in original,
                }
            )
        elif entries:
            entries[-1]["rest"].append(line)
            entries[-1]["lines"].append(line)
        # Text before the first header (rare) has no date or author and is ignored

    day_first = _day_first([e["date"] for e in entries])
    result = ParseResult()
    counter = OccurrenceCounter()

    def text_message(sent_at, author: str, body: str, raw: dict) -> NormalizedMessage:
        occurrence = counter.next(sent_at, author, body)
        return NormalizedMessage(
            source=SOURCE,
            external_id=stable_id(SOURCE, channel, sent_at.isoformat(), author, body, occurrence),
            channel=channel,
            author=author,
            sent_at=sent_at,
            text=body,
            raw=raw,
        )

    for entry in entries:
        match = AUTHOR_RE.match("\n".join(entry["rest"]))
        if not match:  # system line: encryption notice, "X added Y"...
            result.skipped += 1
            continue
        author = match["author"].strip()
        body = EDITED_RE.sub("", match["text"].strip())
        raw = {"filename": filename, "line": entry["line"], "lines": entry["lines"]}
        first, _, caption = body.partition("\n")
        caption = caption.strip()
        attached = ATTACHED_RE.match(first.strip())
        if attached:
            sent_at = as_utc(_parse_datetime(entry["date"], entry["time"], day_first))
            name = (attached["ios"] or attached["android"]).strip()
            if audio_mime(name):
                occurrence = counter.next(sent_at, author, first.strip())
                parent_id = stable_id(SOURCE, channel, sent_at.isoformat(), author, first.strip(), occurrence)
                result.messages.append(
                    voice_message(
                        source=SOURCE, parent_id=parent_id, channel=channel, author=author, sent_at=sent_at,
                        attachment=_voice_attachment(name, media), raw=raw,
                    )
                )
            elif not caption:
                result.skipped += 1  # a photo, video or document without a caption
            if caption:
                result.messages.append(text_message(sent_at, author, caption, raw))
            continue
        if entry["system"] or not body or SKIP_TEXT_RE.match(body):  # iOS system line, media placeholder
            result.skipped += 1
            continue
        sent_at = as_utc(_parse_datetime(entry["date"], entry["time"], day_first))
        result.messages.append(text_message(sent_at, author, body, raw))
    return result


class WhatsAppExportConnector(FileConnector):
    source = SOURCE

    def parse(self) -> ParseResult:
        return parse_whatsapp(decode_text(self.data), self.filename)


def find_chat(archive: zipfile.ZipFile) -> zipfile.ZipInfo | None:
    """The chat .txt of a WhatsApp ZIP export: _chat.txt (iOS), "WhatsApp Chat with X.txt" (Android), or any
    .txt that looks like a chat."""
    texts = [info for info in members(archive) if basename(info.filename).lower().endswith(".txt")]
    for info in texts:
        name = basename(info.filename).lower()
        if name == "_chat.txt" or name.startswith("whatsapp chat"):
            return info
    for info in texts:
        if info.file_size <= MAX_TEXT_BYTES and is_whatsapp_text(decode_text(archive.read(info))):
            return info
    return None


class WhatsAppZipConnector(FileConnector):
    """A chat exported "with media": the chat .txt and its files in one ZIP."""

    source = SOURCE

    def parse(self) -> ParseResult:
        archive = open_zip(self.data)
        chat = find_chat(archive)
        if chat is None:
            raise ValueError("No WhatsApp chat (.txt) was found in this ZIP.")
        text = decode_text(read_text_member(archive, chat))
        name = basename(chat.filename)
        # iOS names the chat file _chat.txt; then the ZIP's name ("WhatsApp Chat - Acme.zip") names the chat
        chat_file = self.filename if name.lower() == "_chat.txt" else name
        return parse_whatsapp(text, chat_file, media=ZipMedia(archive))
