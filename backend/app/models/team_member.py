from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, JsonType

TEAM_ROLES = ("lead", "developer", "qa")


class TeamMember(Base):
    """A person working on a project. Not necessarily a ReqGuard user."""

    __tablename__ = "team_members"
    __table_args__ = (
        UniqueConstraint("project_id", "email"),
        CheckConstraint(f"role IN {TEAM_ROLES}", name="role"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(255))
    # List of strings; JSON (not ARRAY) so it works on SQLite and PostgreSQL
    skills: Mapped[list[str]] = mapped_column(JsonType, default=list)
    role: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    project: Mapped["Project"] = relationship(back_populates="members")  # noqa: F821
