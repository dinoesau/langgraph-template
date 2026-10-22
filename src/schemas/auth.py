"""Auth wire shapes: Pydantic only, ORM never crosses the HTTP seam."""

import re
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, EmailStr, Field, SecretStr, field_validator

_PASSWORD_RE = re.compile(r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z0-9]).{8,}$")
_MAX_USERNAME_CHARS = 32


def _clean_name(raw: str) -> str:
    """Collapse whitespace so logs plus prompts plus keys stay small."""
    return " ".join(raw.strip().split())


class RegisterIn(BaseModel):
    """Inbound registration, parsed once at the HTTP edge."""

    email: Annotated[EmailStr, Field(max_length=254)]
    password: SecretStr
    username: str | None = None

    @field_validator("email")
    @classmethod
    def _lower_email(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("password")
    @classmethod
    def _strong_password(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value()
        if len(secret) > 128:
            raise ValueError("password must not exceed 128 characters")
        if not _PASSWORD_RE.match(secret):
            raise ValueError("password must be 8 plus upper plus lower plus digit plus symbol")
        return value

    @field_validator("username")
    @classmethod
    def _clean_username(cls, value: str | None) -> str | None:
        if value is None:
            return None
        clean = _clean_name(value)[:_MAX_USERNAME_CHARS]
        return clean or None


class TokenOut(BaseModel):
    """Outbound token, no ORM types cross here."""

    access_token: str
    token_type: str = "bearer"
    expires_at: int


class SessionOut(BaseModel):
    """Outbound session creation, token is session-scoped."""

    session_id: str
    token: str
    token_type: str = "bearer"


class SessionInfo(BaseModel):
    """One session row for list responses."""

    id: str
    name: str | None = None
    username: str | None = None
    created_at: datetime | None = None


class RenameIn(BaseModel):
    """Inbound rename, parsed once at the HTTP edge."""

    name: str

    @field_validator("name")
    @classmethod
    def _clean_session_name(cls, value: str) -> str:
        clean = _clean_name(value)
        if not clean:
            raise ValueError("name must not be blank")
        return clean[:255]
