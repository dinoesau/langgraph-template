"""Unit fakes for S4 auth crypto: password rules, bcrypt hashes, JWT scopes."""

import uuid

import jwt
import pytest

from src.core.security import (
    SESSION_SCOPE,
    USER_SCOPE,
    AuthError,
    create_token,
    decode_token,
    hash_password,
    validate_password_strength,
    verify_password,
)

SECRET = "test-secret-" + "x" * 32


@pytest.mark.parametrize(
    "password",
    [
        "Short1!",
        "alllowercase1!",
        "ALLUPPERCASE1!",
        "NoDigitsHere!",
        "NoSymbols123",
        "        ",
    ],
)
def test_weak_passwords_rejected(password: str) -> None:
    with pytest.raises(ValueError):
        validate_password_strength(password)


def test_strong_password_accepted() -> None:
    validate_password_strength("ChangeMe123!")


def test_hash_roundtrip_and_wrong_password() -> None:
    hashed = hash_password("ChangeMe123!")
    assert hashed != "ChangeMe123!"
    assert verify_password("ChangeMe123!", hashed) is True
    assert verify_password("WrongPass123!", hashed) is False


def test_hash_uses_bcrypt_format() -> None:
    assert hash_password("ChangeMe123!").startswith("$2")


def test_user_token_carries_expiry_and_unique_jti() -> None:
    first, first_exp = create_token("7", USER_SCOPE, SECRET, expires_days=7)
    second, _ = create_token("7", USER_SCOPE, SECRET, expires_days=7)
    assert first_exp is not None
    first_claims = decode_token(first, SECRET, USER_SCOPE)
    second_claims = decode_token(second, SECRET, USER_SCOPE)
    assert first_claims["sub"] == "7"
    assert first_claims["jti"] != second_claims["jti"]
    assert uuid.UUID(hex=first_claims["jti"])


def test_session_token_binds_session_id() -> None:
    token, _ = create_token("7", SESSION_SCOPE, SECRET, 7, extra={"sid": "sess-1"})
    claims = decode_token(token, SECRET, SESSION_SCOPE)
    assert claims["sid"] == "sess-1"
    assert claims["sub"] == "7"


def test_wrong_scope_rejected() -> None:
    token, _ = create_token("7", USER_SCOPE, SECRET, 7)
    with pytest.raises(AuthError):
        decode_token(token, SECRET, SESSION_SCOPE)


def test_wrong_secret_rejected() -> None:
    token, _ = create_token("7", USER_SCOPE, SECRET, 7)
    with pytest.raises(AuthError):
        decode_token(token, "other-secret-" + "y" * 32, USER_SCOPE)


def test_expired_token_rejected_with_independent_source() -> None:
    from datetime import datetime, timedelta

    expired = jwt.encode(
        {
            "sub": "7",
            "scope": USER_SCOPE,
            "jti": uuid.uuid4().hex,
            "iat": datetime.now() - timedelta(days=2),
            "exp": datetime.now() - timedelta(days=1),
        },
        SECRET,
        algorithm="HS256",
    )
    with pytest.raises(AuthError):
        decode_token(expired, SECRET, USER_SCOPE)


def test_tampered_token_rejected() -> None:
    token, _ = create_token("7", USER_SCOPE, SECRET, 7)
    with pytest.raises(AuthError):
        decode_token(token[:-2] + ("ab" if not token.endswith("ab") else "cd"), SECRET, USER_SCOPE)
