"""Unit tests for auth schemas: strength plus names at the edge."""

import pytest
from pydantic import SecretStr, ValidationError

from schemas.auth import RegisterIn, RenameIn


def test_register_accepts_strong_password() -> None:
    body = RegisterIn(
        email="You@Example.com", password=SecretStr("ChangeMe123!"), username="  you  "
    )
    assert body.email == "you@example.com"
    assert body.username == "you"


def test_register_rejects_weak_password() -> None:
    with pytest.raises(ValidationError):
        RegisterIn(email="a@b.co", password=SecretStr("weakpass"), username=None)


def test_register_rejects_short_password() -> None:
    with pytest.raises(ValidationError):
        RegisterIn(email="a@b.co", password=SecretStr("Aa1!"), username=None)


def test_register_blank_username_becomes_none() -> None:
    body = RegisterIn(email="a@b.co", password=SecretStr("ChangeMe123!"), username="   ")
    assert body.username is None


def test_rename_rejects_blank() -> None:
    with pytest.raises(ValidationError):
        RenameIn(name="   ")
