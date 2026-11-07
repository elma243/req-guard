from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

PAIR_RELATIONS = (
    "same",
    "duplicate",
    "conflict",
    "change_request",
    "related_but_different",
    "unrelated",
    "not_enough_information",
)


class PairVerdict(Base):
    """The LLM's answer about how two requirement versions relate (app/nlp/pair_checks.py).

    Versions never change, so the answer is kept and the pair is not asked again. It is about the text only;
    whether a relation becomes a conflict or a change request depends on the older one being accepted.
    """

    __tablename__ = "requirement_pair_verdicts"
    __table_args__ = (
        UniqueConstraint("older_version_id", "newer_version_id"),
        CheckConstraint(f"relation IN {PAIR_RELATIONS}", name="relation"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    older_version_id: Mapped[int] = mapped_column(
        ForeignKey("requirement_versions.id", ondelete="CASCADE"), index=True
    )
    newer_version_id: Mapped[int] = mapped_column(
        ForeignKey("requirement_versions.id", ondelete="CASCADE"), index=True
    )
    relation: Mapped[str] = mapped_column(String(30))
    reason: Mapped[str] = mapped_column(Text)
    # provider:model that answered, e.g. "groq:openai/gpt-oss-120b"
    ai_model: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
