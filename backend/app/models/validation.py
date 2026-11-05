from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.requirement import ORIGINS

FINDING_CATEGORIES = ("vague", "missing_info", "out_of_scope", "module_alignment")
FINDING_SEVERITIES = ("low", "medium", "high")
FINDING_STATUSES = ("suggested", "accepted", "edited", "rejected")


class ValidationFinding(Base):
    """A possible problem with a requirement, found by a validation rule (CLAUDE.md rule 2: a suggestion).

    A re-run only changes findings that are still origin "ai" and status "suggested"; see app/nlp/validate.py.
    """

    __tablename__ = "validation_findings"
    __table_args__ = (
        CheckConstraint(f"category IN {FINDING_CATEGORIES}", name="category"),
        CheckConstraint(f"severity IN {FINDING_SEVERITIES}", name="severity"),
        CheckConstraint(f"origin IN {ORIGINS}", name="origin"),
        CheckConstraint(f"status IN {FINDING_STATUSES}", name="status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    requirement_id: Mapped[int] = mapped_column(ForeignKey("requirements.id", ondelete="CASCADE"), index=True)
    # The requirement version that was checked
    version: Mapped[int] = mapped_column(Integer)
    category: Mapped[str] = mapped_column(String(20))
    # Stable name of the rule, e.g. "vague_word"; with the evidence it identifies a finding across re-runs
    rule: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str] = mapped_column(String(10))
    reason: Mapped[str] = mapped_column(Text)
    # The matched word, or the sentence the finding is about
    evidence: Mapped[str] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(String(10), default="ai")
    status: Mapped[str] = mapped_column(String(20), default="suggested")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    requirement: Mapped["Requirement"] = relationship(back_populates="findings")  # noqa: F821
