"""Register, log in and read the current user."""

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.core.config import settings
from app.core.security import create_access_token, hash_password, verify_password
from app.models import User
from app.schemas.auth import LoginIn, RegisterIn, TokenOut, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(body: RegisterIn, db: DbSession) -> User:
    if db.scalar(select(User).where(User.email == body.email)) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="An account with this email already exists")
    user = User(name=body.name, email=body.email, password_hash=hash_password(body.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, db: DbSession) -> TokenOut:
    user = db.scalar(select(User).where(User.email == body.email))
    # Same message for unknown email and wrong password
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password")
    return TokenOut(
        access_token=create_access_token(user.id, user.token_version),
        expires_in=settings.jwt_expire_minutes * 60,
    )


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> User:
    return user
