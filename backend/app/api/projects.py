"""Project CRUD. Every endpoint only sees projects owned by the current user."""

from fastapi import APIRouter, Response, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, OwnedProject
from app.models import Project
from app.schemas.project import ProjectCreate, ProjectOut, ProjectUpdate

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=list[ProjectOut])
def list_projects(db: DbSession, user: CurrentUser) -> list[Project]:
    return list(db.scalars(select(Project).where(Project.owner_id == user.id).order_by(Project.id.desc())))


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(body: ProjectCreate, db: DbSession, user: CurrentUser) -> Project:
    project = Project(**body.model_dump(), owner_id=user.id)
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project: OwnedProject) -> Project:
    return project


@router.patch("/{project_id}", response_model=ProjectOut)
def update_project(body: ProjectUpdate, project: OwnedProject, db: DbSession) -> Project:
    for field, value in body.model_dump(exclude_unset=True, exclude_none=True).items():
        if field == "jira_task_project_key" and value == "":
            value = None
        setattr(project, field, value)
    db.commit()
    db.refresh(project)
    return project


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project: OwnedProject, db: DbSession) -> Response:
    db.delete(project)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
