"""Team member CRUD inside a project. Only the project owner can use these endpoints."""

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.deps import DbSession, OwnedProject
from app.models import Project, Task, TeamMember
from app.schemas.team import TeamMemberCreate, TeamMemberOut, TeamMemberUpdate

router = APIRouter(prefix="/projects/{project_id}/team", tags=["team"])


def _get_member(db: Session, project: Project, member_id: int) -> TeamMember:
    member = db.get(TeamMember, member_id)
    if member is None or member.project_id != project.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Team member not found")
    return member


def _ensure_email_free(db: Session, project: Project, email: str, exclude_id: int | None = None) -> None:
    query = select(TeamMember.id).where(TeamMember.project_id == project.id, TeamMember.email == email)
    if exclude_id is not None:
        query = query.where(TeamMember.id != exclude_id)
    if db.scalar(query) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="A team member with this email already exists")


@router.get("", response_model=list[TeamMemberOut])
def list_members(project: OwnedProject) -> list[TeamMember]:
    return project.members


@router.post("", response_model=TeamMemberOut, status_code=status.HTTP_201_CREATED)
def add_member(body: TeamMemberCreate, project: OwnedProject, db: DbSession) -> TeamMember:
    _ensure_email_free(db, project, body.email)
    member = TeamMember(**body.model_dump(), project_id=project.id)
    db.add(member)
    db.commit()
    db.refresh(member)
    return member


@router.patch("/{member_id}", response_model=TeamMemberOut)
def update_member(member_id: int, body: TeamMemberUpdate, project: OwnedProject, db: DbSession) -> TeamMember:
    member = _get_member(db, project, member_id)
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    if "email" in changes:
        _ensure_email_free(db, project, changes["email"], exclude_id=member.id)
    for field, value in changes.items():
        setattr(member, field, value)
    db.commit()
    db.refresh(member)
    return member


@router.delete("/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_member(member_id: int, project: OwnedProject, db: DbSession) -> Response:
    """The member's open tasks (Step 9) go back to "not assigned"; the database clears the member from tasks."""
    member = _get_member(db, project, member_id)
    db.execute(
        update(Task)
        .where(Task.assignee_id == member.id, Task.status.in_(("assigned", "in_progress")))
        .values(status="suggested")
    )
    db.execute(
        update(Task)
        .where(Task.suggested_assignee_id == member.id, Task.suggestion_status == "suggested")
        .values(suggestion_status=None, suggestion_reason="The suggested member was removed from the team.")
    )
    db.delete(member)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
