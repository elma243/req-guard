"""Clarification questions for the client, the share links that carry them, the client's answers, the AI's
proposed updates from those answers, and requirement approvals (Step 8A: AR-04, FR-09, FR-22).

Nothing here is ever sent to the client by the app: the lead copies a link and shares it themselves.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    false,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, JsonType
from app.models.requirement import ORIGINS

# Rule 2 statuses. A question the lead puts in a link becomes "accepted"; one the lead edits becomes "edited".
QUESTION_STATUSES = ("suggested", "edited", "accepted", "rejected")
PROPOSAL_STATUSES = ("suggested", "accepted", "edited", "rejected")

link_questions = Table(
    "clarification_link_questions",
    Base.metadata,
    Column("link_id", ForeignKey("clarification_links.id", ondelete="CASCADE"), primary_key=True),
    Column("question_id", ForeignKey("clarification_questions.id", ondelete="CASCADE"), primary_key=True),
)


class ClarificationQuestion(Base):
    """A short, polite question for the client about one requirement, linked to the finding it resolves.

    `finding_id` is None for a question the lead wrote without a finding, or once a re-validation removed the
    finding (the problem may be resolved).
    """

    __tablename__ = "clarification_questions"
    __table_args__ = (
        CheckConstraint(f"origin IN {ORIGINS}", name="origin"),
        CheckConstraint(f"status IN {QUESTION_STATUSES}", name="status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    requirement_id: Mapped[int] = mapped_column(ForeignKey("requirements.id", ondelete="CASCADE"), index=True)
    finding_id: Mapped[int | None] = mapped_column(
        ForeignKey("validation_findings.id", ondelete="SET NULL"), index=True
    )
    text: Mapped[str] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(20), default="suggested")
    # provider:model that wrote it; None for questions written by a person
    ai_model: Mapped[str | None] = mapped_column(String(100))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    requirement: Mapped["Requirement"] = relationship(back_populates="questions")  # noqa: F821
    finding: Mapped["ValidationFinding | None"] = relationship()  # noqa: F821
    links: Mapped[list["ClarificationLink"]] = relationship(secondary=link_questions, back_populates="questions")
    answers: Mapped[list["ClarificationAnswer"]] = relationship(
        back_populates="question", passive_deletes=True, order_by="ClarificationAnswer.id"
    )


class ClarificationLink(Base):
    """A no-login page for the client with the questions the lead chose. Single use: closed after one submission.

    Only a keyed hash of the token is stored, so the link is shown to the lead once, when it is created.
    """

    __tablename__ = "clarification_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # When the client sent their answers; the link is closed from then on
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    project: Mapped["Project"] = relationship()  # noqa: F821
    questions: Mapped[list[ClarificationQuestion]] = relationship(
        secondary=link_questions, back_populates="links", order_by=ClarificationQuestion.id
    )


class ClarificationAnswer(Base):
    """The client's answer to one question, exactly as received (English, Bangla or Banglish; rule 10)."""

    __tablename__ = "clarification_answers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question_id: Mapped[int] = mapped_column(
        ForeignKey("clarification_questions.id", ondelete="CASCADE"), index=True
    )
    requirement_id: Mapped[int] = mapped_column(ForeignKey("requirements.id", ondelete="CASCADE"), index=True)
    link_id: Mapped[int | None] = mapped_column(ForeignKey("clarification_links.id", ondelete="SET NULL"))
    text: Mapped[str] = mapped_column(Text)
    answered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # When the lead first saw it; None = a new answer (the "New answers" badge)
    seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    question: Mapped[ClarificationQuestion] = relationship(back_populates="answers")
    requirement: Mapped["Requirement"] = relationship(back_populates="answers")  # noqa: F821


class ClarificationProposal(Base):
    """An updated requirement text the AI proposes from the client's answers (rule 2: a suggestion).

    Accepting it creates a new requirement version written by the lead (`version_id`).
    """

    __tablename__ = "clarification_proposals"
    __table_args__ = (
        CheckConstraint(f"origin IN {ORIGINS}", name="origin"),
        CheckConstraint(f"status IN {PROPOSAL_STATUSES}", name="status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    requirement_id: Mapped[int] = mapped_column(ForeignKey("requirements.id", ondelete="CASCADE"), index=True)
    # The requirement version the proposal was written for; accepting it later than that is refused
    based_on_version: Mapped[int] = mapped_column(Integer)
    answer_ids: Mapped[list[int]] = mapped_column(JsonType, default=list, server_default="[]")
    # None when the AI answered "not enough information"
    statement: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text, default="")
    not_enough_information: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    ai_model: Mapped[str | None] = mapped_column(String(100))
    origin: Mapped[str] = mapped_column(String(10), default="ai")
    status: Mapped[str] = mapped_column(String(20), default="suggested")
    decided_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version_id: Mapped[int | None] = mapped_column(ForeignKey("requirement_versions.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    requirement: Mapped["Requirement"] = relationship(back_populates="proposals")  # noqa: F821


class RequirementApproval(Base):
    """The lead approved one version of a requirement. Separate from "accepted"; kept as a record.

    It counts only while `revoked_at` is None and the version is still the current one, so any new version ends
    it. `override_reason` is set when open High findings were approved over; `open_high_finding_ids` keeps them.
    """

    __tablename__ = "requirement_approvals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    requirement_id: Mapped[int] = mapped_column(ForeignKey("requirements.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    approved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    override_reason: Mapped[str | None] = mapped_column(Text)
    open_high_finding_ids: Mapped[list[int]] = mapped_column(JsonType, default=list, server_default="[]")
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(String(100))

    requirement: Mapped["Requirement"] = relationship(back_populates="approvals")  # noqa: F821
    approved_by: Mapped["User | None"] = relationship()  # noqa: F821

    @property
    def approved_by_name(self) -> str | None:
        return self.approved_by.name if self.approved_by else None
