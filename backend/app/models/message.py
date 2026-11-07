from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, JsonType


class Message(Base):
    """One client message in the unified format (CLAUDE.md rule 1).

    `raw` keeps the original record from the export or API so nothing about the source is lost.
    Re-importing the same message is prevented by the (project_id, source, external_id) key.
    """

    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("project_id", "source", "external_id"),
        Index("ix_messages_project_id_sent_at", "project_id", "sent_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id", ondelete="SET NULL"), index=True)
    source: Mapped[str] = mapped_column(String(30))
    external_id: Mapped[str] = mapped_column(String(255))
    channel: Mapped[str] = mapped_column(String(200), default="")
    author: Mapped[str] = mapped_column(String(200), default="")
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    text: Mapped[str] = mapped_column(Text)
    raw: Mapped[dict] = mapped_column(JsonType, default=dict)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # When requirement extraction last read this message (also set when it gave no requirements)
    requirements_extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
