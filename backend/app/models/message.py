from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.connectors.base import KIND_TEXT, KIND_VOICE  # noqa: F401  (one definition of the kinds)
from app.core.db import Base, JsonType

# Transcription of a voice message (app/ai/speech.py). None for text messages.
TRANSCRIPT_PENDING = "pending"  # waiting for the transcription worker
TRANSCRIPT_RUNNING = "transcribing"  # claimed by the worker (a claim older than 15 minutes is stale)
TRANSCRIPT_DONE = "done"
TRANSCRIPT_FAILED = "failed"  # can be retried
TRANSCRIPT_SKIPPED = "skipped"  # cannot be transcribed (too large, audio not in the upload); not retried


class Message(Base):
    """One client message in the unified format (CLAUDE.md rule 1).

    `raw` keeps the original record from the export or API so nothing about the source is lost.
    Re-importing the same message is prevented by the (project_id, source, external_id) key.

    A voice message (kind "voice") is one audio file; its `text` is the transcript ("" until transcribed) and
    `raw["voice"]` keeps the file id/name, duration, detected language and the model that transcribed it.
    """

    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("project_id", "source", "external_id"),
        Index("ix_messages_project_id_sent_at", "project_id", "sent_at"),
        Index("ix_messages_transcript_status_transcribe_after", "transcript_status", "transcribe_after"),
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
    kind: Mapped[str] = mapped_column(String(10), default=KIND_TEXT, server_default=KIND_TEXT)
    transcript_status: Mapped[str | None] = mapped_column(String(20))
    transcript_error: Mapped[str | None] = mapped_column(Text)
    # Failed transcription attempts so far; after MAX_ATTEMPTS the message is marked failed
    transcript_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Not transcribed before this time (backoff after a rate limit); also the claim time while transcribing
    transcribe_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    audio: Mapped["VoiceAudio | None"] = relationship(  # noqa: F821
        back_populates="message", cascade="all, delete-orphan", passive_deletes=True, uselist=False
    )
