"""Unit tests for auth store: CRUD plus isolation on ephemeral SQLite."""

import asyncio
from collections.abc import Coroutine
from typing import Any, TypeVar

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from models.base import Base
from models.session import Session
from models.user import User
from services.auth_security import hash_password
from services.auth_store import (
    auth_session_factory,
    authenticate_user,
    create_auth_engine,
    create_session,
    create_user,
    delete_session,
    get_session,
    get_user_by_email,
    list_sessions,
    normalize_auth_url,
    rename_session,
)

_T = TypeVar("_T")


def _run(coro: Coroutine[Any, Any, _T]) -> _T:
    return asyncio.run(coro)


async def _fresh_db() -> tuple[AsyncEngine, AsyncSession]:
    engine = create_auth_engine("sqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = auth_session_factory(engine)
    session = factory()
    return engine, session


def test_create_and_get_user() -> None:
    async def _body() -> None:
        engine, db = await _fresh_db()
        try:
            created = await create_user(db, "a@b.co", "you", hash_password("ChangeMe123!"))
            found = await get_user_by_email(db, "a@b.co")
            assert found is not None
            assert found.id == created.id
            assert found.email == "a@b.co"
        finally:
            await db.close()
            await engine.dispose()

    _run(_body())


def test_authenticate_ok_and_bad() -> None:
    async def _body() -> None:
        engine, db = await _fresh_db()
        try:
            await create_user(db, "a@b.co", None, hash_password("ChangeMe123!"))
            ok = await authenticate_user(db, "a@b.co", "ChangeMe123!")
            assert ok is not None
            assert await authenticate_user(db, "a@b.co", "Wrong123!x") is None
            assert await authenticate_user(db, "missing@b.co", "ChangeMe123!") is None
        finally:
            await db.close()
            await engine.dispose()

    _run(_body())


def test_session_crud_scoped_by_user() -> None:
    async def _body() -> None:
        engine, db = await _fresh_db()
        try:
            alice = await create_user(db, "alice@b.co", "alice", hash_password("ChangeMe123!"))
            bob = await create_user(db, "bob@b.co", "bob", hash_password("ChangeMe123!"))
            created = await create_session(db, alice.id, alice.username)
            assert created.username == "alice"
            assert await get_session(db, created.id) is not None
            assert await rename_session(db, bob.id, created.id, "hijack") is None
            renamed = await rename_session(db, alice.id, created.id, "research")
            assert renamed is not None
            assert renamed.name == "research"
            assert [s.id for s in await list_sessions(db, alice.id)] == [created.id]
            assert await list_sessions(db, bob.id) == []
            assert not await delete_session(db, bob.id, created.id)
            assert await delete_session(db, alice.id, created.id)
            assert await get_session(db, created.id) is None
        finally:
            await db.close()
            await engine.dispose()

    _run(_body())


def test_normalize_auth_url_cases() -> None:
    assert normalize_auth_url("postgres://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert normalize_auth_url("postgresql://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert normalize_auth_url("  sqlite:///:memory:  ") == "sqlite+aiosqlite:///:memory:"
    assert normalize_auth_url("sqlite+aiosqlite:///:memory:") == "sqlite+aiosqlite:///:memory:"


def test_models_match_initial_migration() -> None:
    users = User.metadata.tables["users"]
    sessions = User.metadata.tables["sessions"]
    assert set(users.columns.keys()) == {
        "id",
        "email",
        "username",
        "hashed_password",
        "created_at",
    }
    assert set(sessions.columns.keys()) == {
        "id",
        "user_id",
        "name",
        "username",
        "created_at",
    }
    assert "ix_sessions_user_id" in {index.name for index in sessions.indexes}
