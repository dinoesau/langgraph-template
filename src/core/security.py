"""Password and JWT helpers for the S4 auth lifecycle.

Hashing uses bcrypt; tokens are HS256 JWTs with a unique ``jti`` claim and a
``scope`` claim (``user`` for identity, ``session`` for conversation scope).
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import TypedDict

import bcrypt
import jwt

USER_SCOPE = "user"
SESSION_SCOPE = "session"


class AuthError(ValueError):
    """Raised when a credential or token fails validation."""


class TokenClaims(TypedDict, total=False):
    """Decoded JWT claims; ``sid`` is present only on session tokens."""

    sub: str
    scope: str
    jti: str
    sid: str


def validate_password_strength(password: str) -> None:
    """Enforce 8+ chars with upper, lower, digit, and symbol; raise on failure."""
    if (
        len(password) < 8
        or not any(ch.isupper() for ch in password)
        or not any(ch.islower() for ch in password)
        or not any(ch.isdigit() for ch in password)
        or not any(not ch.isalnum() for ch in password)
    ):
        raise ValueError("Password must have 8+ characters with upper, lower, digit, and symbol.")


def hash_password(password: str) -> str:
    """Return the bcrypt hash; never store the plain password."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    """Compare a plain password against its bcrypt hash."""
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


def create_token(
    subject: str,
    scope: str,
    secret: str,
    expires_days: int,
    extra: dict[str, str] | None = None,
) -> tuple[str, datetime]:
    """Mint a scoped JWT with unique ``jti``; return the token and expiry."""
    now = datetime.now(UTC)
    expires_at = now + timedelta(days=expires_days)
    payload: dict[str, object] = {
        "sub": subject,
        "scope": scope,
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": expires_at,
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, secret, algorithm="HS256"), expires_at


def decode_token(token: str, secret: str, expected_scope: str | None = None) -> TokenClaims:
    """Verify signature and expiry; when a scope is given, enforce it too."""
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise AuthError("Invalid or expired token.") from exc
    if not isinstance(payload, dict):
        raise AuthError("Invalid or expired token.")
    scope = payload.get("scope")
    if not isinstance(scope, str) or not scope:
        raise AuthError("Token is missing required claims.")
    if expected_scope is not None and scope != expected_scope:
        raise AuthError("Token scope mismatch.")
    subject = payload.get("sub")
    jti = payload.get("jti")
    if not isinstance(subject, str) or not subject or not isinstance(jti, str) or not jti:
        raise AuthError("Token is missing required claims.")
    claims: TokenClaims = {"sub": subject, "scope": scope, "jti": jti}
    sid = payload.get("sid")
    if isinstance(sid, str) and sid:
        claims["sid"] = sid
    return claims
