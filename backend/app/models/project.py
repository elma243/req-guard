from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    # What the client agreed to build; used later to flag out-of-scope requirements
    scope_text: Mapped[str] = mapped_column(Text, default="")
    # The product's parts, each {"name", "description"}; requirements are matched against them in validation
    modules: Mapped[list[dict]] = mapped_column(JSON, default=list, server_default="[]")
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # Last REQ-number handed out; only grows, so requirement codes are never reused
    requirement_counter: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    owner: Mapped["User"] = relationship(back_populates="projects")  # noqa: F821
    members: Mapped[list["TeamMember"]] = relationship(  # noqa: F821
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="TeamMember.id",
    )
    sources: Mapped[list["Source"]] = relationship(  # noqa: F821
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Source.id",
    )
