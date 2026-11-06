"""Register, verify the email, log in, reset a forgotten password and read the current user."""

import hmac
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentUser, DbSession
from app.connectors.base import as_utc
from app.core import email as mailer
from app.core.config import settings
from app.core.ratelimit import client_ip, enforce, limit_auth, limiter
from app.core.security import (
    create_access_token,
    generate_otp,
    generate_reset_token,
    hash_password,
    hash_secret,
    verify_dummy_password,
    verify_password,
)
from app.models import EmailOtp, PasswordResetToken, User
from app.schemas.auth import (
    PASSWORD_EQUALS_EMAIL,
    EmailIn,
    LoginIn,
    MessageOut,
    RegisterIn,
    ResetPasswordIn,
    TokenOut,
    UserOut,
    VerifyEmailIn,
)

router = APIRouter(prefix="/auth", tags=["auth"])

OTP_EXPIRE_MINUTES = 10
OTP_MAX_ATTEMPTS = 5
RESEND_COOLDOWN_SECONDS = 60
RESET_EXPIRE_MINUTES = 30

EMAIL_TAKEN = "An account with this email already exists. Sign in or reset your password."
BAD_LOGIN = "Email or password is incorrect"
NOT_VERIFIED = "Please verify your email"
# One message for a wrong, expired or used-up code and an unknown email, so the answer reveals no account
BAD_CODE = "That code is incorrect or has expired. Check it or request a new one."
CODE_RESENT = "If this account still needs verifying, we sent a new code."
RESET_SENT = "If this email exists, we sent a link"
BAD_RESET_LINK = "This reset link is invalid or has expired. Please ask for a new one."


def _now() -> datetime:
    return datetime.now(UTC)


def _find_user(db: DbSession, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == email))


def _otp_hash(user_id: int, code: str) -> str:
    return hash_secret(f"otp:{user_id}:{code}")


def _reset_hash(token: str) -> str:
    return hash_secret(f"reset:{token}")


def _resend_key(email: str) -> str:
    return f"otp-cooldown:{email}"


def _issue_otp(db: DbSession, user: User, background: BackgroundTasks) -> None:
    """Replace the user's code with a new one and email it. The plain code is never stored."""
    code = generate_otp()
    db.execute(delete(EmailOtp).where(EmailOtp.user_id == user.id))
    db.add(
        EmailOtp(
            user_id=user.id,
            code_hash=_otp_hash(user.id, code),
            expires_at=_now() + timedelta(minutes=OTP_EXPIRE_MINUTES),
        )
    )
    db.commit()
    # After the response: a slow mail server must not make known emails answer slower
    background.add_task(mailer.deliver, user.email, mailer.otp_email(user.name, code, OTP_EXPIRE_MINUTES))


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(body: RegisterIn, request: Request, db: DbSession, background: BackgroundTasks) -> User:
    limit_auth(request, "register", body.email)
    if _find_user(db, body.email) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=EMAIL_TAKEN)
    user = User(name=body.name, email=body.email, password_hash=hash_password(body.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError:  # the same email registered at the same moment
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail=EMAIL_TAKEN) from None
    db.refresh(user)
    _issue_otp(db, user, background)
    limiter.hit(_resend_key(user.email), 1, RESEND_COOLDOWN_SECONDS)  # the first code starts the cooldown
    return user


@router.post("/verify-email", response_model=MessageOut)
def verify_email(body: VerifyEmailIn, request: Request, db: DbSession) -> MessageOut:
    limit_auth(request, "verify", body.email)
    bad_code = HTTPException(status.HTTP_400_BAD_REQUEST, detail=BAD_CODE)

    user = _find_user(db, body.email)
    otp = db.scalar(select(EmailOtp).where(EmailOtp.user_id == user.id)) if user else None
    if otp is None or as_utc(otp.expires_at) <= _now():
        raise bad_code

    # Counted in one statement before the comparison, so parallel guesses cannot pass the limit
    counted = db.execute(
        update(EmailOtp)
        .where(EmailOtp.id == otp.id, EmailOtp.attempts < OTP_MAX_ATTEMPTS)
        .values(attempts=EmailOtp.attempts + 1)
    )
    db.commit()
    if counted.rowcount == 0 or not hmac.compare_digest(otp.code_hash, _otp_hash(user.id, body.code)):
        raise bad_code

    user.email_verified_at = _now()
    db.delete(otp)
    db.commit()
    return MessageOut(message="Your email is verified. You can sign in now.")


@router.post("/resend-code", response_model=MessageOut)
def resend_code(body: EmailIn, request: Request, db: DbSession, background: BackgroundTasks) -> MessageOut:
    limit_auth(request, "resend", body.email)
    # Applies to every address, known or not, so the wait reveals nothing
    enforce(_resend_key(body.email), 1, RESEND_COOLDOWN_SECONDS, "Please wait before asking for a new code.")
    user = _find_user(db, body.email)
    if user is not None and user.email_verified_at is None:
        _issue_otp(db, user, background)
    return MessageOut(message=CODE_RESENT)


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, request: Request, db: DbSession) -> TokenOut:
    limit_auth(request, "login", body.email)
    bad_login = HTTPException(status.HTTP_401_UNAUTHORIZED, detail=BAD_LOGIN)

    user = _find_user(db, body.email)
    if user is None:
        verify_dummy_password(body.password)  # takes as long as checking a real account
        raise bad_login
    if not verify_password(body.password, user.password_hash):
        raise bad_login
    # Only after the right password, so this cannot be used to discover accounts
    if user.email_verified_at is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=NOT_VERIFIED)
    return TokenOut(
        access_token=create_access_token(user.id, user.token_version),
        expires_in=settings.jwt_expire_minutes * 60,
    )


@router.post("/forgot-password", response_model=MessageOut)
def forgot_password(body: EmailIn, request: Request, db: DbSession, background: BackgroundTasks) -> MessageOut:
    limit_auth(request, "forgot", body.email)
    user = _find_user(db, body.email)
    if user is not None:
        token = generate_reset_token()
        db.execute(delete(PasswordResetToken).where(PasswordResetToken.user_id == user.id))
        db.add(
            PasswordResetToken(
                user_id=user.id,
                token_hash=_reset_hash(token),
                expires_at=_now() + timedelta(minutes=RESET_EXPIRE_MINUTES),
            )
        )
        db.commit()
        # From settings, never the request's Host header, which a caller could set to their own site
        url = f"{settings.frontend_url.rstrip('/')}/reset-password?token={token}"
        background.add_task(mailer.deliver, user.email, mailer.reset_email(user.name, url, RESET_EXPIRE_MINUTES))
    return MessageOut(message=RESET_SENT)


@router.post("/reset-password", response_model=MessageOut)
def reset_password(body: ResetPasswordIn, request: Request, db: DbSession) -> MessageOut:
    enforce(f"reset:ip:{client_ip(request)}", settings.auth_rate_limit_per_minute, 60)
    bad_link = HTTPException(status.HTTP_400_BAD_REQUEST, detail=BAD_RESET_LINK)

    reset = db.scalar(select(PasswordResetToken).where(PasswordResetToken.token_hash == _reset_hash(body.token)))
    if reset is None or reset.used_at is not None or as_utc(reset.expires_at) <= _now():
        raise bad_link
    user = db.get(User, reset.user_id)
    if body.password.strip().lower() == user.email:
        # Same shape as a validation error; the schema cannot check this, it does not know the email
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=[{"type": "invalid", "loc": ["body", "password"], "msg": PASSWORD_EQUALS_EMAIL}],
        )
    password_hash = hash_password(body.password)

    # Claimed in one statement, so of two requests with the same link only one gets through
    claimed = db.execute(
        update(PasswordResetToken)
        .where(PasswordResetToken.id == reset.id, PasswordResetToken.used_at.is_(None))
        .values(used_at=_now())
    )
    if claimed.rowcount == 0:
        db.rollback()
        raise bad_link

    user.password_hash = password_hash
    user.token_version = User.token_version + 1  # ends every session from before the reset
    # The link went to the mailbox, which proves ownership as well as the code would
    user.email_verified_at = user.email_verified_at or _now()
    user.failed_logins = 0
    user.locked_until = None
    db.execute(delete(EmailOtp).where(EmailOtp.user_id == user.id))
    db.commit()
    return MessageOut(message="Your password has been reset. You can sign in now.")


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> User:
    return user
