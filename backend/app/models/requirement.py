from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
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
    # Why it was rejected (optional). Kept through accept and undo, so undoing brings the reason back
    reject_reason: Mapped[str | None] = mapped_column(Text)
    # The status before the last accept / reject, which undo returns to. None when there is nothing to undo
    previous_status: Mapped[str | None] = mapped_column(String(20))
    # Latest validation (app/nlp/validate.py). Here and not on the version, because version rows are only ever
    # added and a re-run can change the result for the same text (for example when the modules change).
    # 0-100; None = not validated yet
    quality_score: Mapped[int | None] = mapped_column(Integer)
    # The criteria that failed: [{"criterion", "points", "reason"}]
    quality_failed: Mapped[list[dict]] = mapped_column(JSON, default=list, server_default="[]")
    # Best-matching module of the project and how well it matched (0-1); None when none matches
    module_name: Mapped[str | None] = mapped_column(String(100))
    module_score: Mapped[float | None] = mapped_column(Float)
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
        foreign_keys="RequirementSource.requirement_id",
    )
    findings: Mapped[list["ValidationFinding"]] = relationship(  # noqa: F821
        back_populates="requirement",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ValidationFinding.id",
    )

    @property
    def current(self) -> "RequirementVersion":
        return self.versions[-1]

    @property
    def open_findings(self) -> int:
        """Findings no person has rejected."""
        return sum(1 for f in self.findings if f.status != "rejected")

    @property
    def has_issues(self) -> bool:
        """A closed requirement (rejected, merged) needs no more work, whatever the rules found."""
        return self.status not in ("rejected", "merged") and bool(self.open_findings or self.quality_failed)


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
    # provider:model that wrote this version (e.g. "groq:openai/gpt-oss-120b"), "rule-based" for the
    # fallback rules; None for versions written by a user
    ai_model: Mapped[str | None] = mapped_column(String(100))
    origin: Mapped[str] = mapped_column(String(10))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    requirement: Mapped[Requirement] = relationship(back_populates="versions")
    created_by: Mapped["User | None"] = relationship()  # noqa: F821

    @property
    def created_by_name(self) -> str | None:
        return self.created_by.name if self.created_by else None


class RequirementSource(Base):
    """Where a requirement came from: a message and the exact sentence of it, unchanged (rule 10)."""

    __tablename__ = "requirement_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    requirement_id: Mapped[int] = mapped_column(ForeignKey("requirements.id", ondelete="CASCADE"), index=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), index=True)
    source_sentence: Mapped[str] = mapped_column(Text)
    # The requirement this row was copied from by a merge; None for the requirement's own sources.
    # Restoring that requirement removes the copy again.
    merged_from_id: Mapped[int | None] = mapped_column(ForeignKey("requirements.id", ondelete="SET NULL"))

    requirement: Mapped[Requirement] = relationship(back_populates="sources", foreign_keys=[requirement_id])
    message: Mapped["Message"] = relationship()  # noqa: F821
