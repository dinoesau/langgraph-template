"""Auth persistence: engine plus CRUD returning internal DTOs.

Handlers map DTOs to schemas, so ORM never crosses the HTTP seam.
Engine uses psycopg async for Postgres and aiosqlite for tests.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from models.session import Session
from models.user import User
from services.auth_security import averify_password


@dataclass(frozen=True)
class UserDTO:
    """Detached user for service-to-handler handoff."""

    id: int
    email: str
    username: str | None


@dataclass(frozen=True)
class SessionDTO:
    """Detached session for service-to-handler handoff."""

    id: str
    user_id: int
    name: str | None
    username: str | None
    created_at: datetime | None = None


def normalize_auth_url(raw: str) -> str:
    """Map app URLs to async SQLAlchemy dialects for auth engine."""
    uri = raw.strip()
    if uri.startswith("postgres://"):
        uri = "postgresql://" + uri[len("postgres://") :]
    if uri.startswith("postgresql://"):
        return "postgresql+psycopg://" + uri[len("postgresql://") :]
    if (uri == "sqlite:///:memory:" or uri.startswith("sqlite:///")) and ("+aiosqlite" not in uri):
        return uri.replace("sqlite://", "sqlite+aiosqlite://", 1)
    return uri


def create_auth_engine(url: str, pool_size: int = 5, max_overflow: int = 10) -> AsyncEngine:
    """Build one async engine for user plus session rows."""
    normalized = normalize_auth_url(url)
    if ":memory:" in normalized:
        return create_async_engine(
            normalized,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    return create_async_engine(normalized, pool_size=pool_size, max_overflow=max_overflow)


def auth_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Return a session factory with no commit side effects on read."""
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def _to_user_dto(user: User) -> UserDTO:
    return UserDTO(id=user.id, email=user.email, username=user.username)


def _to_session_dto(session: Session) -> SessionDTO:
    return SessionDTO(
        id=session.id,
        user_id=session.user_id,
        name=session.name,
        username=session.username,
        created_at=session.created_at,
    )


async def create_user(db: AsyncSession, email: str, username: str | None, pw_hash: str) -> UserDTO:
    """Insert one user row and return its detached view."""
    user = User(email=email, username=username, hashed_password=pw_hash)
    db.add(user)
    await db.flush()
    return _to_user_dto(user)


async def _fetch_user_row(db: AsyncSession, email: str) -> User | None:
    """Return one user row by email for auth checks."""
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def get_user_by_email(db: AsyncSession, email: str) -> UserDTO | None:
    """Return one user by email or None when missing."""
    user = await _fetch_user_row(db, email)
    return _to_user_dto(user) if user is not None else None


async def get_user_by_id(db: AsyncSession, user_id: int) -> UserDTO | None:
    """Return one user by id or None when missing."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    return _to_user_dto(user) if user is not None else None


async def authenticate_user(db: AsyncSession, email: str, password: str) -> UserDTO | None:
    """Check credentials with timing mitigation, None on any failure."""
    from services.auth_security import DUMMY_HASH

    user = await _fetch_user_row(db, email)
    if user is None:
        await averify_password(password, DUMMY_HASH)
        return None
    if not await averify_password(password, user.hashed_password):
        return None
    return _to_user_dto(user)


async def create_session(db: AsyncSession, user_id: int, username: str | None) -> SessionDTO:
    """Insert one session row copying username to skip a join on chat."""
    session = Session(id=uuid.uuid4().hex, user_id=user_id, username=username)
    db.add(session)
    await db.flush()
    return _to_session_dto(session)


async def list_sessions(db: AsyncSession, user_id: int) -> list[SessionDTO]:
    """List caller sessions ordered by creation."""
    result = await db.execute(
        select(Session).where(Session.user_id == user_id).order_by(Session.created_at, Session.id)
    )
    return [_to_session_dto(row) for row in result.scalars().all()]


async def get_session(db: AsyncSession, session_id: str) -> SessionDTO | None:
    """Return one session by id or None when missing."""
    result = await db.execute(select(Session).where(Session.id == session_id))
    session = result.scalar_one_or_none()
    return _to_session_dto(session) if session is not None else None


async def _owned_session(db: AsyncSession, user_id: int, session_id: str) -> Session | None:
    """Return one session when owned by the caller, else None."""
    result = await db.execute(select(Session).where(Session.id == session_id))
    session = result.scalar_one_or_none()
    if session is None or session.user_id != user_id:
        return None
    return session


async def rename_session(
    db: AsyncSession, user_id: int, session_id: str, name: str
) -> SessionDTO | None:
    """Rename one owned session, None when missing or not owned."""
    session = await _owned_session(db, user_id, session_id)
    if session is None:
        return None
    session.name = name
    await db.flush()
    return _to_session_dto(session)


async def delete_session(db: AsyncSession, user_id: int, session_id: str) -> bool:
    """Delete one owned session row, false when missing or not owned."""
    session = await _owned_session(db, user_id, session_id)
    if session is None:
        return False
    await db.delete(session)
    await db.flush()
    return True
