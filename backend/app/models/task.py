"""Task groups and tasks (Step 9: FR-11, AR-06).

Approved requirements are grouped by module, or by embedding clusters the AI names (app/tasks/grouping.py), and
each becomes one task. The AI only suggests an assignee (`suggested_assignee_id`); `assignee_id` is set only when
the lead accepts, changes or picks one (CLAUDE.md rule 2). Grouping adds tasks for new requirements and never
changes a task or group that exists.
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
    false,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, JsonType
from app.models.requirement import ORIGINS

GROUP_KINDS = ("module", "cluster", "unmatched")
GROUP_STATUSES = ("suggested", "accepted", "edited")
TASK_STATUSES = ("suggested", "assigned", "in_progress", "done")
# Statuses that count as work a member has on their plate
OPEN_STATUSES = ("assigned", "in_progress")
SUGGESTION_STATUSES = ("suggested", "accepted", "changed", "rejected")


def task_code(number: int) -> str:
    return f"T-{number:03d}"


class TaskGroup(Base):
    """A set of tasks that belong together: one module of the project, or a cluster of similar requirements."""

    __tablename__ = "task_groups"
    __table_args__ = (
        CheckConstraint(f"kind IN {GROUP_KINDS}", name="kind"),
        CheckConstraint(f"origin IN {ORIGINS}", name="origin"),
        CheckConstraint(f"status IN {GROUP_STATUSES}", name="status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(20))
    # The project module this group stands for (kind "module" only)
    module_name: Mapped[str | None] = mapped_column(String(100))
    # Why these requirements are together
    reason: Mapped[str] = mapped_column(Text, default="")
    origin: Mapped[str] = mapped_column(String(10), default="ai")
    status: Mapped[str] = mapped_column(String(20), default="suggested")
    # provider:model that named the group; "rule-based" when the name came from its most frequent words; None for
    # module groups and names a person gave
    ai_model: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    tasks: Mapped[list["Task"]] = relationship(
        back_populates="group", cascade="all, delete-orphan", passive_deletes=True, order_by="Task.number"
    )


class Task(Base):
    """The work for one approved requirement; its test cases are the requirement's."""

    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint("project_id", "number"),
        UniqueConstraint("requirement_id"),
        CheckConstraint(f"status IN {TASK_STATUSES}", name="status"),
        CheckConstraint(f"group_origin IN {ORIGINS}", name="group_origin"),
        CheckConstraint(
            f"suggestion_status IS NULL OR suggestion_status IN {SUGGESTION_STATUSES}", name="suggestion_status"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("task_groups.id", ondelete="CASCADE"), index=True)
    requirement_id: Mapped[int] = mapped_column(ForeignKey("requirements.id", ondelete="CASCADE"))
    # T-<number>; from Project.task_counter, so never reused
    number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="suggested")
    # Why the task is in its group; "user" origin once the lead moved it
    grouping_reason: Mapped[str] = mapped_column(Text, default="")
    group_origin: Mapped[str] = mapped_column(String(10), default="ai")
    # Set only by the lead
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("team_members.id", ondelete="SET NULL"), index=True)
    # The AI's suggestion: best and second-best member, with the reason and score of the best
    suggested_assignee_id: Mapped[int | None] = mapped_column(ForeignKey("team_members.id", ondelete="SET NULL"))
    alternative_assignee_id: Mapped[int | None] = mapped_column(ForeignKey("team_members.id", ondelete="SET NULL"))
    suggestion_reason: Mapped[str | None] = mapped_column(Text)
    suggestion_score: Mapped[float | None] = mapped_column(Float)
    suggestion_status: Mapped[str | None] = mapped_column(String(20))
    # Members the lead turned down for this task; they are not suggested for it again
    rejected_member_ids: Mapped[list[int]] = mapped_column(JsonType, default=list, server_default="[]")
    # Set when the requirement got a new version or lost its approval; the lead clears it
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    needs_review_reason: Mapped[str | None] = mapped_column(Text)
    # The requirement version the task was made for
    requirement_version: Mapped[int] = mapped_column(Integer)
    jira_issue_key: Mapped[str | None] = mapped_column(String(50))
    jira_pushed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    group: Mapped[TaskGroup] = relationship(back_populates="tasks")
    requirement: Mapped["Requirement"] = relationship(back_populates="task")  # noqa: F821
    assignee: Mapped["TeamMember | None"] = relationship(foreign_keys=[assignee_id])  # noqa: F821
    suggested_assignee: Mapped["TeamMember | None"] = relationship(  # noqa: F821
        foreign_keys=[suggested_assignee_id]
    )
    alternative_assignee: Mapped["TeamMember | None"] = relationship(  # noqa: F821
        foreign_keys=[alternative_assignee_id]
    )

    @property
    def code(self) -> str:
        return task_code(self.number)

    @property
    def has_pending_suggestion(self) -> bool:
        return (
            self.assignee_id is None
            and self.suggested_assignee_id is not None
            and self.suggestion_status == "suggested"
        )
