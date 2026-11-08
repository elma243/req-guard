from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, JsonType

SYNC_OK = "ok"
SYNC_ERROR = "error"
SYNC_NEEDS_AUTH = "needs_auth"

# Minutes between automatic syncs a user can choose
SYNC_INTERVALS = (1, 5, 15, 60)
DEFAULT_SYNC_INTERVAL = 5


class Source(Base):
    """Where messages come from: an uploaded chat export, a file, or an API connector.

    `config` holds non-secret settings only (e.g. a channel id or file name) plus the sync cursor.
    Tokens never go there: per-source credentials (Gmail refresh token) are stored encrypted in
    `credentials_encrypted`, which is never returned by the API.
    """

    __tablename__ = "sources"
    __table_args__ = (
        UniqueConstraint("project_id", "type", "display_name"),
        CheckConstraint("sync_interval_minutes IN (1, 5, 15, 60)", name="sync_interval_minutes"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    # Uploads: whatsapp | messenger | email | meeting_notes. APIs: jira | slack | gmail | telegram | discord
    type: Mapped[str] = mapped_column(String(30))
    display_name: Mapped[str] = mapped_column(String(200))
    config: Mapped[dict] = mapped_column(JsonType, default=dict)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    credentials_encrypted: Mapped[str | None] = mapped_column(Text)
    # Result of the last API sync: ok | error | needs_auth (None for uploads and never-synced sources)
    sync_status: Mapped[str | None] = mapped_column(String(20))
    sync_error: Mapped[str | None] = mapped_column(Text)
    # Automatic sync (app/connectors/scheduler.py). Telegram defaults to on: it keeps updates for ~24 hours only
    auto_sync: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    sync_interval_minutes: Mapped[int] = mapped_column(
        Integer, default=DEFAULT_SYNC_INTERVAL, server_default=str(DEFAULT_SYNC_INTERVAL)
    )
    next_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The lock: set while a sync (manual or automatic) runs; one older than LOCK_TIMEOUT counts as stale
    sync_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Failed syncs in a row; at MAX_FAILURES auto-sync pauses until a sync succeeds or the user turns it on again
    failure_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    auto_sync_paused: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    project: Mapped["Project"] = relationship(back_populates="sources")  # noqa: F821
