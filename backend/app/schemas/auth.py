"""Auth request and response bodies. These rules are the source of truth; frontend/lib/authRules.ts mirrors them.

Failures use PydanticCustomError, so the message reaches the user as written (a plain ValueError gets the prefix
"Value error, "). Each error names its field in `loc`, which the frontend shows under that input.
"""

import re
from datetime import datetime
from typing import Annotated, Any

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    WrapValidator,
    field_validator,
)
from pydantic_core import PydanticCustomError

from app.schemas.common import Email

NAME_MIN = 2
NAME_MAX = 60
PASSWORD_MIN = 8
PASSWORD_MAX = 128

PASSWORD_EQUALS_EMAIL = "Password must not be the same as your email."


def _invalid(message: str) -> PydanticCustomError:
    return PydanticCustomError("invalid", message)


def _check_name(value: str) -> str:
    name = value.strip()
    if not name:
        raise _invalid("Name is required.")
    if not NAME_MIN <= len(name) <= NAME_MAX:
        raise _invalid(f"Name must be {NAME_MIN} to {NAME_MAX} characters.")
    return name


def _check_email(value: Any, handler) -> str:
    text = value.strip() if isinstance(value, str) else value
    if text == "":
        raise _invalid("Email is required.")
    try:
        return handler(text)
    except ValidationError:
        raise _invalid("Enter a valid email address.") from None


def _check_password(value: str) -> str:
    if len(value) < PASSWORD_MIN:
        raise _invalid(f"Password must be at least {PASSWORD_MIN} characters.")
    if len(value) > PASSWORD_MAX:
        raise _invalid(f"Password must be at most {PASSWORD_MAX} characters.")
    if not any(c.isalpha() for c in value):
        raise _invalid("Password must contain a letter.")
    if not any(c.isdecimal() for c in value):
        raise _invalid("Password must contain a number.")
    return value


def _check_code(value: str) -> str:
    code = value.strip()
    if not re.fullmatch(r"[0-9]{6}", code):
        raise _invalid("Enter the 6-digit code.")
    return code


def _check_confirmation(value: str, info: ValidationInfo) -> str:
    # `password` is missing from info.data when it failed its own rules; that error is enough
    password = info.data.get("password")
    if password is not None and value != password:
        raise _invalid("Passwords do not match.")
    return value


AccountName = Annotated[str, AfterValidator(_check_name)]
AccountEmail = Annotated[Email, WrapValidator(_check_email)]
NewPassword = Annotated[str, AfterValidator(_check_password)]
OtpCode = Annotated[str, AfterValidator(_check_code)]


class RegisterIn(BaseModel):
    name: AccountName
    email: AccountEmail
    password: NewPassword
    confirm_password: str

    @field_validator("password")
    @classmethod
    def password_differs_from_email(cls, value: str, info: ValidationInfo) -> str:
        email = info.data.get("email")
        if email is not None and value.strip().lower() == email:
            raise _invalid(PASSWORD_EQUALS_EMAIL)
        return value

    _confirmation = field_validator("confirm_password")(_check_confirmation)


class LoginIn(BaseModel):
    email: AccountEmail
    # No strength rules here: accounts older than these rules must still be able to log in
    password: str = Field(min_length=1, max_length=PASSWORD_MAX)


class EmailIn(BaseModel):
    email: AccountEmail


class VerifyEmailIn(BaseModel):
    email: AccountEmail
    code: OtpCode


class ResetPasswordIn(BaseModel):
    token: str = Field(min_length=1, max_length=128)
    password: NewPassword
    confirm_password: str

    _confirmation = field_validator("confirm_password")(_check_confirmation)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


class MessageOut(BaseModel):
    message: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    email: str
    created_at: datetime
