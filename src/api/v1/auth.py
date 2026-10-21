"""Auth endpoints: register plus login plus session lifecycle."""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import SessionContext, get_db, require_scope
from core.config import get_settings
from schemas.auth import RegisterIn, RenameIn, SessionInfo, SessionOut, TokenOut
from services.auth_security import create_token, hash_password
from services.auth_store import (
    authenticate_user,
    create_session,
    create_user,
    delete_session,
    get_user_by_email,
    get_user_by_id,
    list_sessions,
    rename_session,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _expiry_ts(minutes: int) -> int:
    return int((datetime.now(UTC) + timedelta(minutes=minutes)).timestamp())


@router.post("/register", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterIn,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenOut:
    """Create one account and return its user token."""
    settings = get_settings()
    if await get_user_by_email(db, str(body.email)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email is taken")
    try:
        created = await create_user(
            db,
            str(body.email),
            body.username,
            hash_password(body.password.get_secret_value()),
        )
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email is taken") from None
    token = create_token(
        str(created.id),
        "user",
        None,
        settings.jwt_user_expire_minutes,
        secret=settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    return TokenOut(access_token=token, expires_at=_expiry_ts(settings.jwt_user_expire_minutes))


@router.post("/login", response_model=TokenOut)
async def login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenOut:
    """Exchange credentials for one user token with generic errors."""
    settings = get_settings()
    user = await authenticate_user(db, form.username.strip().lower(), form.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = create_token(
        str(user.id),
        "user",
        None,
        settings.jwt_user_expire_minutes,
        secret=settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    return TokenOut(access_token=token, expires_at=_expiry_ts(settings.jwt_user_expire_minutes))


@router.post("/session", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
async def create_chat_session(
    ctx: Annotated[SessionContext, Depends(require_scope("user"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SessionOut:
    """Create one session row and return its session-scoped token."""
    settings = get_settings()
    user = await get_user_by_id(db, int(ctx.user_id))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Could not validate credentials"
        )
    created = await create_session(db, int(ctx.user_id), user.username)
    await db.commit()
    token = create_token(
        ctx.user_id,
        "session",
        created.id,
        settings.jwt_session_expire_minutes,
        secret=settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    return SessionOut(session_id=created.id, token=token)


@router.get("/sessions", response_model=list[SessionInfo])
async def list_chat_sessions(
    ctx: Annotated[SessionContext, Depends(require_scope("user"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[SessionInfo]:
    """List caller sessions, never other users rows."""
    rows = await list_sessions(db, int(ctx.user_id))
    return [
        SessionInfo(id=row.id, name=row.name, username=row.username, created_at=row.created_at)
        for row in rows
    ]


@router.patch("/session/{session_id}/name", response_model=SessionInfo)
async def rename_chat_session(
    session_id: str,
    body: RenameIn,
    ctx: Annotated[SessionContext, Depends(require_scope("session"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SessionInfo:
    """Rename one owned session bound to its session token."""
    if ctx.session_id != session_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Session scope mismatch")
    renamed = await rename_session(db, int(ctx.user_id), session_id, body.name)
    if renamed is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    await db.commit()
    return SessionInfo(
        id=renamed.id, name=renamed.name, username=renamed.username, created_at=renamed.created_at
    )


@router.delete("/session/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat_session(
    session_id: str,
    ctx: Annotated[SessionContext, Depends(require_scope("session"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Delete one owned session bound to its session token."""
    if ctx.session_id != session_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Session scope mismatch")
    if not await delete_session(db, int(ctx.user_id), session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    await db.commit()
