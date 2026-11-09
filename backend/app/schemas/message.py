from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints

from app.schemas.common import UtcDatetime


class MessageOut(BaseModel):
    """A message in lists. `raw` is left out to keep pages small."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    source_id: int | None
    source: str
    external_id: str
    channel: str
    author: str
    sent_at: UtcDatetime
    text: str
    imported_at: UtcDatetime


class MessagePage(BaseModel):
    items: list[MessageOut]
    total: int
    page: int
    page_size: int


class SourceOut(BaseModel):
    """A source. Credentials are never part of it."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    type: str
    display_name: str
    config: dict
    last_synced_at: UtcDatetime | None
    created_at: UtcDatetime
    sync_status: str | None = None
    sync_error: str | None = None
    message_count: int = 0
    # API sources only: true while a Gmail source has no connected Google account
    needs_auth: bool = False
    # Automatic sync (API sources only)
    auto_sync: bool = False
    sync_interval_minutes: int = 5
    next_sync_at: UtcDatetime | None = None
    failure_count: int = 0
    auto_sync_paused: bool = False
    # A sync (manual or automatic) is running right now
    syncing: bool = False
    # False when the server runs without the scheduler (AUTO_SYNC_ENABLED=false)
    auto_sync_available: bool = True


class AutoSyncUpdate(BaseModel):
    """Only the fields that are sent are changed."""

    enabled: bool | None = None
    interval_minutes: Literal[1, 5, 15, 60] | None = None


class UnseenMessages(BaseModel):
    count: int


class ConnectorOut(BaseModel):
    """An API connector type. `missing_env` lists variable names only, never values."""

    type: str
    label: str
    configured: bool
    missing_env: list[str]


class SourceCreate(BaseModel):
    type: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=30)]
    display_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    # Connector-specific, non-secret settings (e.g. {"project_key": "ACME"}); checked by the connector
    settings: dict[str, Any] = {}


class SourceStatus(BaseModel):
    id: int
    type: str
    configured: bool
    needs_auth: bool
    sync_status: str | None
    sync_error: str | None
    last_synced_at: UtcDatetime | None
    message_count: int


class SyncResult(BaseModel):
    imported: int
    duplicates: int
    voice_queued: int = 0
    sync_status: str
    last_synced_at: UtcDatetime | None


class AuthorizeUrl(BaseModel):
    url: str


class FileImportResult(BaseModel):
    filename: str
    type: str | None  # detected parser; None when the file is not supported
    imported: int = 0
    duplicates: int = 0
    skipped: int = 0
    # New voice notes: waiting for transcription, and ones that cannot be transcribed (reason on the message)
    voice_queued: int = 0
    voice_skipped: int = 0
    error: str | None = None


class UploadResult(BaseModel):
    imported: int
    duplicates: int
    skipped: int
    voice_queued: int = 0
    voice_skipped: int = 0
    files: list[FileImportResult]
