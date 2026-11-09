"""Reading uploaded ZIP exports (WhatsApp "with media", the Messenger export folder) safely, without extracting.

Nothing is written to disk: members are only looked up by their file name. Sizes are checked from the ZIP
directory before a member is read: chat files at most MAX_TEXT_BYTES, each audio file at most MAX_AUDIO_BYTES
(larger ones become voice messages marked "too large") and at most MAX_AUDIO_TOTAL of audio per ZIP.
"""

import io
import zipfile
from pathlib import PurePosixPath

from app.connectors.base import MAX_AUDIO_BYTES, AudioAttachment, audio_mime

MAX_TEXT_BYTES = 10 * 1024 * 1024
MAX_AUDIO_TOTAL = 100 * 1024 * 1024
TOTAL_NOTE = "The ZIP holds more than 100 MB of audio; this voice note was not read. Upload the chat in parts."


class ArchiveError(ValueError):
    """The upload is not a readable ZIP (shown to the user like other parse errors)."""


def open_zip(data: bytes) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError):
        raise ArchiveError("This file is not a valid ZIP archive.") from None


def basename(name: str) -> str:
    return PurePosixPath(name.replace("\\", "/")).name


def members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    """Files (not folders) of the ZIP, skipping macOS resource forks."""
    return [
        info
        for info in archive.infolist()
        if not info.is_dir() and "__MACOSX/" not in info.filename and not basename(info.filename).startswith("._")
    ]


def read_text_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    if info.file_size > MAX_TEXT_BYTES:
        raise ArchiveError(f"{basename(info.filename)} in the ZIP is larger than 10 MB.")
    try:
        return archive.read(info)
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError):
        raise ArchiveError(f"Could not read {basename(info.filename)} from the ZIP.") from None


class ZipMedia:
    """Audio files of a ZIP, found by file name."""

    def __init__(self, archive: zipfile.ZipFile):
        self.archive = archive
        self.by_name: dict[str, zipfile.ZipInfo] = {}
        for info in members(archive):
            self.by_name.setdefault(basename(info.filename).lower(), info)
        self.total = 0

    def attachment(self, name: str, missing_note: str, duration: float | None = None) -> AudioAttachment:
        """The audio file `name` (a path or a file name) as an attachment; marked skipped when it cannot be read."""
        filename = basename(name)
        mime = audio_mime(filename) or "audio/mpeg"
        info = self.by_name.get(filename.lower())
        base = {"file_id": filename, "filename": filename, "mime_type": mime, "duration": duration}
        if info is None:
            return AudioAttachment(**base, skip_reason=missing_note)
        if info.file_size > MAX_AUDIO_BYTES:
            return AudioAttachment(**base, size=info.file_size)  # the importer marks it too large
        if self.total + info.file_size > MAX_AUDIO_TOTAL:
            return AudioAttachment(**base, size=info.file_size, skip_reason=TOTAL_NOTE)
        try:
            data = self.archive.read(info)
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError):
            return AudioAttachment(**base, skip_reason=f"Could not read {filename} from the ZIP.")
        self.total += len(data)
        return AudioAttachment(**base, size=len(data), data=data)
