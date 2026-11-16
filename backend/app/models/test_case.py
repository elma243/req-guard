"""Acceptance criteria and Given/When/Then test cases of approved requirements (Step 8B: AR-05, FR-10).

Both follow rules 2 and 6: the AI's items start as `suggested`, a person accepts, edits or rejects them, and an
edit adds a version row instead of changing one. `needs_review` is set when the requirement gets a new version,
because the item was written for the old text; accepting or editing the item clears it.
"""

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

from app.core.db import Base
from app.models.requirement import ORIGINS

ITEM_STATUSES = ("suggested", "accepted", "edited", "rejected")
TEST_CASE_TYPES = ("positive", "negative", "edge")


class AcceptanceCriterion(Base):
    """A testable condition the requirement must meet. Its text lives in versions."""

    __tablename__ = "acceptance_criteria"
    __table_args__ = (
        UniqueConstraint("requirement_id", "number"),
        CheckConstraint(f"origin IN {ORIGINS}", name="origin"),
        CheckConstraint(f"status IN {ITEM_STATUSES}", name="status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    requirement_id: Mapped[int] = mapped_column(ForeignKey("requirements.id", ondelete="CASCADE"), index=True)
    # AC-<number> within the requirement; from Requirement.criterion_counter, so never reused
    number: Mapped[int] = mapped_column(Integer)
    origin: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(20), default="suggested")
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # The requirement version it was written for
    requirement_version: Mapped[int] = mapped_column(Integer)
    # provider:model that wrote it; None for criteria written by a person
    ai_model: Mapped[str | None] = mapped_column(String(100))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    requirement: Mapped["Requirement"] = relationship(back_populates="criteria")  # noqa: F821
    versions: Mapped[list["AcceptanceCriterionVersion"]] = relationship(
        back_populates="criterion",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="AcceptanceCriterionVersion.version",
    )

    @property
    def code(self) -> str:
        return f"AC-{self.number}"

    @property
    def current(self) -> "AcceptanceCriterionVersion":
        return self.versions[-1]


class AcceptanceCriterionVersion(Base):
    """One version of a criterion's text. Rows are only ever added."""

    __tablename__ = "acceptance_criterion_versions"
    __table_args__ = (
        UniqueConstraint("criterion_id", "version"),
        CheckConstraint(f"origin IN {ORIGINS}", name="origin"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    criterion_id: Mapped[int] = mapped_column(
        ForeignKey("acceptance_criteria.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(String(10))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    criterion: Mapped[AcceptanceCriterion] = relationship(back_populates="versions")
    created_by: Mapped["User | None"] = relationship()  # noqa: F821

    @property
    def created_by_name(self) -> str | None:
        return self.created_by.name if self.created_by else None


class TestCase(Base):
    """A Given/When/Then test case that checks one acceptance criterion. Its content lives in versions."""

    __tablename__ = "test_cases"
    __test__ = False  # not a pytest class
    __table_args__ = (
        UniqueConstraint("requirement_id", "number"),
        CheckConstraint(f"origin IN {ORIGINS}", name="origin"),
        CheckConstraint(f"status IN {ITEM_STATUSES}", name="status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    requirement_id: Mapped[int] = mapped_column(ForeignKey("requirements.id", ondelete="CASCADE"), index=True)
    # TC-<number> within the requirement; from Requirement.test_case_counter, so never reused
    number: Mapped[int] = mapped_column(Integer)
    origin: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(20), default="suggested")
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    requirement_version: Mapped[int] = mapped_column(Integer)
    ai_model: Mapped[str | None] = mapped_column(String(100))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    requirement: Mapped["Requirement"] = relationship(back_populates="test_cases")  # noqa: F821
    versions: Mapped[list["TestCaseVersion"]] = relationship(
        back_populates="test_case",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="TestCaseVersion.version",
    )

    @property
    def code(self) -> str:
        return f"TC-{self.number}"

    @property
    def current(self) -> "TestCaseVersion":
        return self.versions[-1]


class TestCaseVersion(Base):
    """One version of a test case. Rows are only ever added."""

    __tablename__ = "test_case_versions"
    __test__ = False
    __table_args__ = (
        UniqueConstraint("test_case_id", "version"),
        CheckConstraint(f"origin IN {ORIGINS}", name="origin"),
        CheckConstraint(f"type IN {TEST_CASE_TYPES}", name="type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    test_case_id: Mapped[int] = mapped_column(ForeignKey("test_cases.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(300))
    type: Mapped[str] = mapped_column(String(20))
    given: Mapped[str] = mapped_column(Text)
    when: Mapped[str] = mapped_column(Text)
    then: Mapped[str] = mapped_column(Text)
    # The criterion it verifies; None once that criterion was removed by a regeneration
    criterion_id: Mapped[int | None] = mapped_column(
        ForeignKey("acceptance_criteria.id", ondelete="SET NULL"), index=True
    )
    origin: Mapped[str] = mapped_column(String(10))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    test_case: Mapped[TestCase] = relationship(back_populates="versions")
    criterion: Mapped[AcceptanceCriterion | None] = relationship()
    created_by: Mapped["User | None"] = relationship()  # noqa: F821

    @property
    def created_by_name(self) -> str | None:
        return self.created_by.name if self.created_by else None
