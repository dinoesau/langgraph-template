"""Auth crypto: password hashing plus JWT mint and verify.

Single token creator keeps expiry plus least scope plus uniqueness in one
place. Scope is space-delimited per OAuth2, sid binds session tokens.
"""

import uuid
from datetime import UTC, datetime, timedelta

import jwt
from jwt.exceptions import InvalidTokenError
from pwdlib import PasswordHash
from pydantic import BaseModel

password_hash = PasswordHash.recommended()
DUMMY_HASH = password_hash.hash("dummy-password-Aa1!")


class TokenData(BaseModel):
    """Decoded identity claims, primitives only so HTTP stays ORM-free."""

    sub: str
    scopes: list[str]
    sid: str | None = None
    jti: str = ""
    exp: int = 0


def hash_password(password: str) -> str:
    """Return the argon2id hash for one clear password."""
    return password_hash.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    """Return true when one clear password matches its stored hash."""
    return password_hash.verify(plain, hashed)


def create_token(
    sub: str,
    scope: str,
    sid: str | None,
    expires_minutes: int,
    *,
    secret: str,
    algorithm: str = "HS256",
) -> str:
    """Mint one JWT with sub plus scope plus sid plus jti plus expiry."""
    if not secret.strip():
        raise RuntimeError("JWT secret must be set to issue tokens")
    now = datetime.now(UTC)
    payload = {
        "sub": sub,
        "scope": scope,
        "sid": sid,
        "jti": uuid.uuid4().hex,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=expires_minutes)).timestamp()),
    }
    return jwt.encode(payload, secret, algorithm=algorithm)


def decode_token(token: str, *, secret: str, algorithms: list[str] | None = None) -> TokenData:
    """Verify one JWT and return its typed claims or raise InvalidTokenError."""
    decoded = jwt.decode(token, secret, algorithms=algorithms or ["HS256"])
    scope_raw = decoded.get("scope", "")
    scopes = [part for part in str(scope_raw).split(" ") if part]
    return TokenData(
        sub=str(decoded.get("sub", "")),
        scopes=scopes,
        sid=decoded.get("sid"),
        jti=str(decoded.get("jti", "")),
        exp=int(decoded.get("exp", 0)),
    )


__all__ = [
    "DUMMY_HASH",
    "InvalidTokenError",
    "TokenData",
    "create_token",
    "decode_token",
    "hash_password",
    "password_hash",
    "verify_password",
]
