from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

ORIGINS = ("ai", "user")
# "merged": folded into another requirement (merged_into_id); kept so nothing is lost
REQUIREMENT_STATUSES = ("suggested", "accepted", "edited", "rejected", "merged")
REQUIREMENT_TYPES = ("functional", "non_functional")
NFR_CATEGORIES = ("performance", "security", "usability", "reliability", "other")


def requirement_code(number: int) -> str:
    return f"REQ-{number:03d}"


class Requirement(Base):
    """A requirement of a project (CLAUDE.md rules 1, 2 and 6).

    The text lives in versions: editing adds a RequirementVersion, it never changes one.
    `number` comes from Project.requirement_counter, so a code is never reused.
    """

    __tablename__ = "requirements"
    __table_args__ = (
        UniqueConstraint("project_id", "number"),
        CheckConstraint(f"origin IN {ORIGINS}", name="origin"),
        CheckConstraint(f"status IN {REQUIREMENT_STATUSES}", name="status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    code: Mapped[str] = mapped_column(String(20))
    origin: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(20), default="suggested")
    merged_into_id: Mapped[int | None] = mapped_column(ForeignKey("requirements.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    versions: Mapped[list["RequirementVersion"]] = relationship(
        back_populates="requirement",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="RequirementVersion.version",
    )
    sources: Mapped[list["RequirementSource"]] = relationship(
        back_populates="requirement",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="RequirementSource.id",
    )

    @property
    def current(self) -> "RequirementVersion":
        return self.versions[-1]


class RequirementVersion(Base):
    """One version of a requirement's content. Rows are only ever added."""

    __tablename__ = "requirement_versions"
    __table_args__ = (
        UniqueConstraint("requirement_id", "version"),
        CheckConstraint(f"origin IN {ORIGINS}", name="origin"),
        CheckConstraint(f"type IN {REQUIREMENT_TYPES}", name="type"),
        CheckConstraint(f"nfr_category IS NULL OR nfr_category IN {NFR_CATEGORIES}", name="nfr_category"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    requirement_id: Mapped[int] = mapped_column(ForeignKey("requirements.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    statement: Mapped[str] = mapped_column(Text)
    type: Mapped[str] = mapped_column(String(20))
    # Only for non-functional requirements
    nfr_category: Mapped[str | None] = mapped_column(String(20))
    actor: Mapped[str] = mapped_column(String(200), default="")
    # The AI's confidence (0-1); None for versions written by a user
    confidence: Mapped[float | None] = mapped_column(Float)
    # The LLM model that wrote this version (e.g. "gemini-3.8-flash"); None for versions written by a user
    ai_model: Mapped[str | None] = mapped_column(String(100))
    origin: Mapped[str] = mapped_column(String(10))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    requirement: Mapped[Requirement] = relationship(back_populates="versions")


class RequirementSource(Base):
    """Where a requirement came from: a message and the exact sentence of it, unchanged (rule 10)."""

    __tablename__ = "requirement_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    requirement_id: Mapped[int] = mapped_column(ForeignKey("requirements.id", ondelete="CASCADE"), index=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), index=True)
    source_sentence: Mapped[str] = mapped_column(Text)

    requirement: Mapped[Requirement] = relationship(back_populates="sources")
    message: Mapped["Message"] = relationship()  # noqa: F821
