"""Pydantic contracts for auth; ORM models are never returned from routes."""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from src.core.security import validate_password_strength


def _normalize_email(email: str) -> str:
    cleaned = email.strip().lower()
    if "@" not in cleaned or "." not in cleaned.split("@")[-1]:
        raise ValueError("Enter a valid email address.")
    return cleaned


class RegisterRequest(BaseModel):
    """Account creation payload with enforced password strength."""

    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=128)
    username: str | None = Field(default=None, max_length=64)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        """Lowercase and strip the email so duplicates match."""
        return _normalize_email(value)

    @field_validator("password")
    @classmethod
    def check_strength(cls, value: str) -> str:
        """Reject weak passwords with a 422 before any hashing."""
        validate_password_strength(value)
        return value

    @field_validator("username")
    @classmethod
    def clean_username(cls, value: str | None) -> str | None:
        """Trim the optional username; blank becomes absent."""
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class RegisterResponse(BaseModel):
    """Created account plus its first user token; never includes a hash."""

    user_id: int
    email: str
    username: str | None
    access_token: str
    expires_at: datetime


class LoginResponse(BaseModel):
    """User token issued by the OAuth2 password grant."""

    access_token: str
    token_type: str = "bearer"
    expires_at: datetime


class SessionCreateRequest(BaseModel):
    """Optional display name for a new chat session."""

    name: str | None = Field(default=None, min_length=1, max_length=128)


class SessionCreateResponse(BaseModel):
    """New session identifier plus its conversation-scoped token."""

    session_id: str
    token: str
    expires_at: datetime


class SessionOut(BaseModel):
    """One caller-owned session; shaped from the ORM, never the ORM itself."""

    session_id: str
    name: str | None
    username: str | None
    created_at: datetime


class SessionListResponse(BaseModel):
    """Caller-owned sessions only."""

    sessions: list[SessionOut]


class RenameSessionRequest(BaseModel):
    """New display name for a session."""

    name: str = Field(min_length=1, max_length=128)


class DeleteSessionResponse(BaseModel):
    """Confirmation that the session row was removed."""

    session_id: str
    deleted: bool = True
