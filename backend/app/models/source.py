from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, JsonType

SYNC_OK = "ok"
SYNC_ERROR = "error"
SYNC_NEEDS_AUTH = "needs_auth"


class Source(Base):
    """Where messages come from: an uploaded chat export, a file, or an API connector.

    `config` holds non-secret settings only (e.g. a channel id or file name) plus the sync cursor.
    Tokens never go there: per-source credentials (Gmail refresh token) are stored encrypted in
    `credentials_encrypted`, which is never returned by the API.
    """

    __tablename__ = "sources"
    __table_args__ = (UniqueConstraint("project_id", "type", "display_name"),)

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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    project: Mapped["Project"] = relationship(back_populates="sources")  # noqa: F821
