"""Trace links from approved requirements to code and tests (Step 10).

A link points at a target by a stable key, not at a snapshot row, so a decision survives re-analysis:
`file` app/auth.py, `test_file` tests/test_auth.py, `test_function` tests/test_auth.py::test_reset, `commit` <sha>,
`pr` 12. There is one row per (requirement, target): a rejected link is never suggested again (rule 2).

origin:
- `explicit`: the requirement's ID (REQ-004) was found next to the target. Plain code, confidence 1.0. It counts
  toward coverage unless a person rejects it.
- `ai`: embedding similarity, checked by the LLM. It counts only once a person confirms it.
- `user`: added by a person; accepted at once.

status `dismissed`: the LLM said "no link" or "not enough information". Hidden, and not asked again for the same
requirement version.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

LINK_TYPES = ("implements", "verifies")
TARGET_TYPES = ("file", "commit", "pr", "test_file", "test_function")
LINK_ORIGINS = ("explicit", "ai", "user")
LINK_STATUSES = ("suggested", "accepted", "rejected", "dismissed")

# Which link type each target type gets
TARGET_LINK_TYPE = {
    "file": "implements",
    "commit": "implements",
    "pr": "implements",
    "test_file": "verifies",
    "test_function": "verifies",
}


class TraceLink(Base):
    __tablename__ = "trace_links"
    __table_args__ = (
        UniqueConstraint("requirement_id", "target_type", "target_key"),
        CheckConstraint(f"link_type IN {LINK_TYPES}", name="link_type"),
        CheckConstraint(f"target_type IN {TARGET_TYPES}", name="target_type"),
        CheckConstraint(f"origin IN {LINK_ORIGINS}", name="origin"),
        CheckConstraint(f"status IN {LINK_STATUSES}", name="status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"), index=True)
    requirement_id: Mapped[int] = mapped_column(ForeignKey("requirements.id", ondelete="CASCADE"), index=True)
    link_type: Mapped[str] = mapped_column(String(20))
    target_type: Mapped[str] = mapped_column(String(20))
    target_key: Mapped[str] = mapped_column(String(1000))
    # What the user sees: a path, "path::test_name", "abc1234 Add login", "#12 Add login"
    target_label: Mapped[str] = mapped_column(Text, default="")
    # Docstring first line or commit / PR title; also used to match Step 8B test cases (TC-n)
    target_detail: Mapped[str | None] = mapped_column(Text)
    target_line: Mapped[int | None] = mapped_column(Integer)
    origin: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(20), default="suggested")
    confidence: Mapped[float | None] = mapped_column(Float)
    similarity: Mapped[float | None] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(Text, default="")
    # Where the requirement ID was found, e.g. "REQ-004 in the commit message"
    evidence: Mapped[str | None] = mapped_column(Text)
    # provider:model that checked the suggestion
    ai_model: Mapped[str | None] = mapped_column(String(100))
    # False for an AI suggestion no LLM could check yet; the next analysis asks again
    llm_checked: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    # The requirement version the link was found for
    requirement_version: Mapped[int] = mapped_column(Integer)
    decided_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    requirement: Mapped["Requirement"] = relationship()  # noqa: F821
    decided_by: Mapped["User | None"] = relationship()  # noqa: F821

    @property
    def live(self) -> bool:
        """Counts toward coverage: accepted, or explicit and not rejected. AI suggestions need a person."""
        if self.status == "accepted":
            return True
        return self.origin == "explicit" and self.status == "suggested"

    @property
    def pending(self) -> bool:
        """An AI suggestion waiting for a person."""
        return self.origin == "ai" and self.status == "suggested"
