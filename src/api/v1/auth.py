"""Auth endpoints: register plus login plus session lifecycle."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Security, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import SessionContext, get_db, require_scope
from core.config import Settings, get_settings
from core.limiter import limiter
from schemas.auth import RegisterIn, RenameIn, SessionInfo, SessionOut, TokenOut
from services.auth_security import ahash_password, create_token, decode_token
from services.auth_store import (
    SessionDTO,
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


def _token_exp(token: str, settings: Settings) -> int:
    """Read exp from the minted token so wire expiry matches token expiry."""
    return decode_token(
        token, secret=settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
    ).exp


def _mint_user_token(user_id: str, settings: Settings) -> TokenOut:
    """Mint one user-scoped token response."""
    token = create_token(
        user_id,
        "user",
        None,
        settings.jwt_user_expire_minutes,
        secret=settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    return TokenOut(access_token=token, expires_at=_token_exp(token, settings))


def _mint_session_token(user_id: str, session_id: str, settings: Settings) -> SessionOut:
    """Mint one session-scoped token response."""
    token = create_token(
        user_id,
        "session",
        session_id,
        settings.jwt_session_expire_minutes,
        secret=settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    return SessionOut(session_id=session_id, token=token)


def _to_session_info(row: SessionDTO) -> SessionInfo:
    """Map one detached session row to its wire shape."""
    return SessionInfo(id=row.id, name=row.name, username=row.username, created_at=row.created_at)


def _require_session_match(ctx: SessionContext, session_id: str) -> None:
    """Reject when the path session differs from the token session."""
    if ctx.session_id != session_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Session scope mismatch")


@router.post("/register", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
@limiter.limit("3/minute")
async def register(
    request: Request,
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
            await ahash_password(body.password.get_secret_value()),
        )
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email is taken") from None
    return _mint_user_token(str(created.id), settings)


@router.post("/login", response_model=TokenOut)
@limiter.limit("5/minute")
async def login(
    request: Request,
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
            # Same shape as the deps challenge so clients parse one format.
            headers={"WWW-Authenticate": 'Bearer realm="auth"'},
        )
    return _mint_user_token(str(user.id), settings)


@router.post("/session", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
async def create_chat_session(
    ctx: Annotated[SessionContext, Security(require_scope("user"), scopes=["user"])],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SessionOut:
    """Create one session row and return its session-scoped token."""
    settings = get_settings()
    user = await get_user_by_id(db, int(ctx.user_id))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    created = await create_session(db, int(ctx.user_id), user.username)
    await db.commit()
    return _mint_session_token(ctx.user_id, created.id, settings)


@router.get("/sessions", response_model=list[SessionInfo])
async def list_chat_sessions(
    ctx: Annotated[SessionContext, Security(require_scope("user"), scopes=["user"])],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[SessionInfo]:
    """List caller sessions, never other users rows."""
    rows = await list_sessions(db, int(ctx.user_id))
    return [_to_session_info(row) for row in rows]


@router.patch("/session/{session_id}/name", response_model=SessionInfo)
async def rename_chat_session(
    session_id: str,
    body: RenameIn,
    ctx: Annotated[SessionContext, Security(require_scope("session"), scopes=["session"])],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SessionInfo:
    """Rename one owned session bound to its session token."""
    _require_session_match(ctx, session_id)
    renamed = await rename_session(db, int(ctx.user_id), session_id, body.name)
    if renamed is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    await db.commit()
    return _to_session_info(renamed)


@router.delete("/session/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat_session(
    session_id: str,
    ctx: Annotated[SessionContext, Security(require_scope("session"), scopes=["session"])],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Delete one owned session bound to its session token."""
    _require_session_match(ctx, session_id)
    if not await delete_session(db, int(ctx.user_id), session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    await db.commit()
