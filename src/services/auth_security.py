"""Auth crypto: password hashing plus JWT mint and verify.

Single token creator keeps expiry plus least scope plus uniqueness in one
place. Scope is space-delimited per OAuth2, sid binds session tokens.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

import jwt
from jwt.exceptions import InvalidTokenError
from pwdlib import PasswordHash
from pydantic import BaseModel

password_hash = PasswordHash.recommended()

_dummy_hash: str | None = None


def _dummy_hash_value() -> str:
    """Return the timing-mitigation hash, computing it once on first use."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = password_hash.hash("dummy-password-Aa1!")
    return _dummy_hash


def __getattr__(name: str) -> str:
    """Serve DUMMY_HASH lazily so imports skip one argon2 hash."""
    if name == "DUMMY_HASH":
        return _dummy_hash_value()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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
    try:
        return bool(password_hash.verify(plain, hashed))
    except Exception:  # noqa: BLE001 - corrupt hashes must fail closed, not 500
        return False


async def ahash_password(password: str) -> str:
    """Hash off the event loop so argon2 never blocks serving."""
    return await asyncio.to_thread(hash_password, password)


async def averify_password(plain: str, hashed: str) -> bool:
    """Verify off the event loop, failing closed on corrupt hashes."""
    return await asyncio.to_thread(verify_password, plain, hashed)


def create_token(
    sub: str,
    scope: str,
    sid: str | None,
    expires_minutes: int,
    *,
    secret: str,
    algorithm: Literal["HS256"] = "HS256",
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


def decode_token(
    token: str, *, secret: str, algorithms: list[Literal["HS256"]] | None = None
) -> TokenData:
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
    "InvalidTokenError",
    "TokenData",
    "ahash_password",
    "averify_password",
    "create_token",
    "decode_token",
    "hash_password",
    "password_hash",
    "verify_password",
]
