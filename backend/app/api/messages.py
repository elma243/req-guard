"""Imported client messages of a project, with filters and pagination. Owner only."""

from datetime import date, datetime, time, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import func, select, update

from app.api.deps import DbSession, OwnedProject
from app.models import Message, Project
from app.schemas.message import MessageOut, MessagePage, UnseenMessages

router = APIRouter(prefix="/projects/{project_id}/messages", tags=["messages"])


def _contains(value: str) -> str:
    """LIKE pattern for 'contains value', with LIKE wildcards in the value escaped."""
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _day_start(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=timezone.utc)


@router.get("", response_model=MessagePage)
def list_messages(
    project: OwnedProject,
    db: DbSession,
    source: Annotated[str | None, Query(max_length=30)] = None,
    author: Annotated[str | None, Query(max_length=200)] = None,
    date_from: date | None = None,
    date_to: date | None = None,
    q: Annotated[str | None, Query(max_length=200, description="Text search")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
) -> MessagePage:
    conditions = [Message.project_id == project.id]
    if source:
        conditions.append(Message.source == source)
    if author and author.strip():
        conditions.append(Message.author.ilike(_contains(author.strip()), escape="\\"))
    if q and q.strip():
        conditions.append(Message.text.ilike(_contains(q.strip()), escape="\\"))
    if date_from:
        conditions.append(Message.sent_at >= _day_start(date_from))
    if date_to:  # inclusive: the whole of that day
        conditions.append(Message.sent_at < _day_start(date_to + timedelta(days=1)))

    total = db.scalar(select(func.count()).select_from(Message).where(*conditions)) or 0
    rows = db.scalars(
        select(Message)
        .where(*conditions)
        .order_by(Message.sent_at.desc(), Message.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return MessagePage(
        items=[MessageOut.model_validate(m) for m in rows], total=total, page=page, page_size=page_size
    )


@router.get("/unseen", response_model=UnseenMessages)
def unseen_messages(project: OwnedProject) -> UnseenMessages:
    """How many messages auto-sync imported since the Messages page was last opened (the menu badge)."""
    return UnseenMessages(count=project.unseen_auto_messages)


@router.post("/seen", response_model=UnseenMessages)
def mark_messages_seen(project: OwnedProject, db: DbSession) -> UnseenMessages:
    """The Messages page was opened: clear the badge."""
    db.execute(
        update(Project)
        .where(Project.id == project.id)
        # A badge change is not an edit of the project, so updated_at stays as it was
        .values(unseen_auto_messages=0, updated_at=Project.updated_at)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    return UnseenMessages(count=0)


@router.delete("/{message_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_message(message_id: int, project: OwnedProject, db: DbSession) -> Response:
    """Permanently delete one imported message (NFR-04)."""
    message = db.get(Message, message_id)
    if message is None or message.project_id != project.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Message not found")
    db.delete(message)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
