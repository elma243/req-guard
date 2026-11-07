"""Password hashing (Argon2id), JWT access tokens (HS256) and one-time secrets (OTP, reset token)."""

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import settings

ALGORITHM = "HS256"

_hasher = PasswordHasher()
# Checked when the email is unknown, so such a login takes as long as a real one
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


class InvalidTokenError(Exception):
    """The token is missing, malformed, expired or signed with another secret."""


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def verify_dummy_password(password: str) -> None:
    """Spend the same time as verify_password when there is no account to check against."""
    verify_password(password, _DUMMY_HASH)


def _secret() -> str:
    if not settings.jwt_secret:
        raise RuntimeError("JWT_SECRET is not set. Add it to backend/.env (see .env.example).")
    return settings.jwt_secret


def generate_otp() -> str:
    """Six random digits; leading zeros are kept."""
    return f"{secrets.randbelow(1_000_000):06d}"


def generate_reset_token() -> str:
    return secrets.token_urlsafe(32)


def hash_secret(value: str) -> str:
    """Keyed hash of a one-time secret. The key matters: six digits alone are reversed in moments."""
    return hmac.new(_secret().encode(), value.encode(), hashlib.sha256).hexdigest()


def create_access_token(user_id: int, token_version: int) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "ver": token_version,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, _secret(), algorithm=ALGORITHM)


OAUTH_STATE_MINUTES = 10


def create_oauth_state(user_id: int, source_id: int) -> str:
    """Signed, short-lived `state` for an OAuth redirect: who started it and for which source."""
    now = datetime.now(UTC)
    payload = {
        "purpose": "oauth_state",
        "sub": str(user_id),
        "source_id": source_id,
        "iat": now,
        "exp": now + timedelta(minutes=OAUTH_STATE_MINUTES),
    }
    return jwt.encode(payload, _secret(), algorithm=ALGORITHM)


def decode_oauth_state(state: str) -> tuple[int, int]:
    """(user_id, source_id) from an OAuth state, or raise InvalidTokenError."""
    try:
        payload = jwt.decode(state, _secret(), algorithms=[ALGORITHM], options={"require": ["sub", "exp"]})
        if payload.get("purpose") != "oauth_state":
            raise InvalidTokenError("not an OAuth state")
        return int(payload["sub"]), int(payload["source_id"])
    except (jwt.PyJWTError, KeyError, ValueError, TypeError) as exc:
        raise InvalidTokenError(str(exc)) from exc


def decode_access_token(token: str) -> tuple[int, int]:
    """Return (user_id, token_version) stored in the token, or raise InvalidTokenError."""
    try:
        payload = jwt.decode(
            token, _secret(), algorithms=[ALGORITHM], options={"require": ["sub", "exp", "ver"]}
        )
        if "purpose" in payload:  # e.g. an OAuth state is not a login token
            raise InvalidTokenError("not an access token")
        return int(payload["sub"]), int(payload["ver"])
    except (jwt.PyJWTError, ValueError, TypeError) as exc:
        raise InvalidTokenError(str(exc)) from exc
