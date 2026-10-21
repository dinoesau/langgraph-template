"""Unit tests for auth crypto: hashing plus JWT claims."""

import time

import pytest
from jwt.exceptions import InvalidTokenError

from services.auth_security import (
    DUMMY_HASH,
    create_token,
    decode_token,
    hash_password,
    verify_password,
)

_SECRET = "test-secret-0123456789abcdef0123456789"
_ALGO = "HS256"


def test_hash_and_verify_roundtrip() -> None:
    hashed = hash_password("ChangeMe123!")
    assert hashed != "ChangeMe123!"
    assert verify_password("ChangeMe123!", hashed)
    assert not verify_password("Wrong123!x", hashed)


def test_dummy_hash_verifies_only_dummy() -> None:
    assert verify_password("dummy-password-Aa1!", DUMMY_HASH)
    assert not verify_password("ChangeMe123!", DUMMY_HASH)


def test_create_requires_secret() -> None:
    with pytest.raises(RuntimeError):
        create_token("1", "user", None, 30, secret="  ", algorithm=_ALGO)


def test_user_token_claims() -> None:
    token = create_token("42", "user", None, 30, secret=_SECRET, algorithm=_ALGO)
    data = decode_token(token, secret=_SECRET)
    assert data.sub == "42"
    assert data.scopes == ["user"]
    assert data.sid is None
    assert data.jti
    assert data.exp > int(time.time())


def test_session_token_carries_sid() -> None:
    token = create_token("7", "session", "sess-1", 60, secret=_SECRET, algorithm=_ALGO)
    data = decode_token(token, secret=_SECRET)
    assert data.scopes == ["session"]
    assert data.sid == "sess-1"


def test_jti_unique_per_token() -> None:
    first = decode_token(
        create_token("1", "user", None, 30, secret=_SECRET, algorithm=_ALGO), secret=_SECRET
    )
    second = decode_token(
        create_token("1", "user", None, 30, secret=_SECRET, algorithm=_ALGO), secret=_SECRET
    )
    assert first.jti != second.jti


def test_wrong_secret_rejected() -> None:
    token = create_token("1", "user", None, 30, secret=_SECRET, algorithm=_ALGO)
    with pytest.raises(InvalidTokenError):
        decode_token(token, secret="other-secret-0123456789abcdef01234567")


def test_malformed_hash_verifies_false() -> None:
    assert not verify_password("anything", "not-a-valid-hash")
