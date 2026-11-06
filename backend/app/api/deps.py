"""Shared FastAPI dependencies: the logged-in user and access to a project."""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.ai.llm import LLMClient, LLMNotConfiguredError, get_llm
from app.core.db import get_db
from app.core.security import InvalidTokenError, decode_access_token
from app.models import Project, User

DbSession = Annotated[Session, Depends(get_db)]

_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthorized
    try:
        user_id, token_version = decode_access_token(credentials.credentials)
    except InvalidTokenError:
        raise unauthorized from None
    user = db.get(User, user_id)
    # An older version means the password was reset after this token was issued
    if user is None or user.token_version != token_version:
        raise unauthorized
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def get_owned_project(project_id: int, db: DbSession, user: CurrentUser) -> Project:
    """The project, if the current user owns it.

    Returns 404 (not 403) for other users' projects so their existence is not revealed.
    """
    project = db.get(Project, project_id)
    if project is None or project.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


OwnedProject = Annotated[Project, Depends(get_owned_project)]


def get_llm_client() -> LLMClient:
    """The configured LLM client; 503 naming the missing .env variables when it is not set up."""
    try:
        return get_llm()
    except LLMNotConfiguredError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from None


LLM = Annotated[LLMClient, Depends(get_llm_client)]


def get_optional_llm_client() -> LLMClient | None:
    """The configured LLM client, or None when no provider is set up (the caller then uses its fallback)."""
    try:
        return get_llm()
    except LLMNotConfiguredError:
        return None


OptionalLLM = Annotated[LLMClient | None, Depends(get_optional_llm_client)]
