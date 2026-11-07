from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.requirement import ORIGINS

# Findings about one requirement (app/nlp/validate.py)
RULE_CATEGORIES = ("vague", "missing_info", "out_of_scope", "module_alignment")
# Findings about a pair of requirements (app/nlp/pair_checks.py); related_requirement_id is the other one
PAIR_CATEGORIES = ("duplicate", "conflict", "change_request")
FINDING_CATEGORIES = RULE_CATEGORIES + PAIR_CATEGORIES
FINDING_SEVERITIES = ("low", "medium", "high")
FINDING_STATUSES = ("suggested", "accepted", "edited", "rejected")


class ValidationFinding(Base):
    """A possible problem with a requirement, found by a validation rule (CLAUDE.md rule 2: a suggestion).

    A re-run only changes findings that are still origin "ai" and status "suggested"; see app/nlp/validate.py
    and app/nlp/pair_checks.py.
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
    # The matched word, or the sentence the finding is about; for a pair finding the other requirement's statement
    evidence: Mapped[str] = mapped_column(Text)
    # Pair findings only: the other requirement (an older one) and the model's score, similarity or contradiction
    related_requirement_id: Mapped[int | None] = mapped_column(
        ForeignKey("requirements.id", ondelete="CASCADE"), index=True
    )
    score: Mapped[float | None] = mapped_column(Float)
    origin: Mapped[str] = mapped_column(String(10), default="ai")
    status: Mapped[str] = mapped_column(String(20), default="suggested")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    requirement: Mapped["Requirement"] = relationship(  # noqa: F821
        back_populates="findings", foreign_keys=[requirement_id]
    )
    related_requirement: Mapped["Requirement | None"] = relationship(  # noqa: F821
        foreign_keys=[related_requirement_id], lazy="joined", innerjoin=False
    )

    @property
    def related_code(self) -> str | None:
        return self.related_requirement.code if self.related_requirement else None

    @property
    def related_statement(self) -> str | None:
        """The other requirement's current statement (evidence holds the one that was checked)."""
        return self.related_requirement.current.statement if self.related_requirement else None
