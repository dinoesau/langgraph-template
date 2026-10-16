"""S4 auth lifecycle: register, login, and session CRUD with scoped JWTs.

User tokens identify the person and gate session create/list; session tokens
identify one conversation and gate chat/rename/delete. Responses are Pydantic
schemas; ORM models never leave this module.
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

from src.api.deps import (
    get_db,
    require_session_claims,
    require_user_claims,
    session_id_from,
)
from src.core.config import get_settings
from src.core.limiter import auth_rate_limiter, client_key
from src.core.security import (
    SESSION_SCOPE,
    USER_SCOPE,
    TokenClaims,
    create_token,
    hash_password,
    verify_password,
)
from src.models import Session as ChatSession
from src.models import User
from src.schemas.auth import (
    DeleteSessionResponse,
    LoginResponse,
    RegisterRequest,
    RegisterResponse,
    RenameSessionRequest,
    SessionCreateRequest,
    SessionCreateResponse,
    SessionListResponse,
    SessionOut,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _check_limit(request: Request) -> None:
    try:
        auth_rate_limiter.check(client_key(request))
    except PermissionError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc


RateLimited = Annotated[None, Depends(_check_limit)]


def _mint(scope: str, user_id: int, session_id: str | None = None) -> tuple[str, datetime]:
    settings = get_settings()
    extra = {"sid": session_id} if session_id is not None else None
    return create_token(
        str(user_id),
        scope,
        settings.JWT_SECRET_KEY,
        settings.JWT_ACCESS_TOKEN_EXPIRE_DAYS,
        extra=extra,
    )


def _to_out(row: ChatSession) -> SessionOut:
    return SessionOut(
        session_id=row.id, name=row.name, username=row.username, created_at=row.created_at
    )


@router.post("/register", response_model=RegisterResponse)
def register(
    payload: RegisterRequest,
    db: Annotated[OrmSession, Depends(get_db)],
    _: RateLimited,
) -> RegisterResponse:
    """Create an account and return its first user token."""
    if db.scalar(select(User).where(User.email == payload.email)) is not None:
        raise HTTPException(status_code=409, detail="Email already registered.")
    user = User(
        email=payload.email,
        username=payload.username,
        hashed_password=hash_password(payload.password),
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Email already registered.") from exc
    db.refresh(user)
    token, expires_at = _mint(USER_SCOPE, user.id)
    return RegisterResponse(
        user_id=user.id,
        email=user.email,
        username=user.username,
        access_token=token,
        expires_at=expires_at,
    )


@router.post("/login", response_model=LoginResponse)
def login(
    db: Annotated[OrmSession, Depends(get_db)],
    _: RateLimited,
    grant_type: Annotated[str, Form()] = "password",
    username: Annotated[str, Form()] = "",
    email: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
) -> LoginResponse:
    """OAuth2 password grant: email (or username) plus password for a user token."""
    if grant_type != "password":
        raise HTTPException(status_code=422, detail="grant_type must be 'password'.")
    identifier = (email or username).strip().lower()
    if not identifier:
        raise HTTPException(status_code=422, detail="Email or username is required.")
    user = db.scalar(select(User).where(or_(User.email == identifier, User.username == identifier)))
    if user is None or not password or not verify_password(password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    token, expires_at = _mint(USER_SCOPE, user.id)
    return LoginResponse(access_token=token, expires_at=expires_at)


@router.post("/session", response_model=SessionCreateResponse)
def create_session(
    db: Annotated[OrmSession, Depends(get_db)],
    claims: Annotated[TokenClaims, Depends(require_user_claims)],
    payload: SessionCreateRequest | None = None,
) -> SessionCreateResponse:
    """Open a chat session for the token owner and return its session token."""
    user = db.get(User, int(claims["sub"]))
    if user is None:
        raise HTTPException(status_code=401, detail="Account no longer exists.")
    row = ChatSession(
        user_id=user.id,
        name=payload.name if payload is not None else None,
        username=user.username,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    token, expires_at = _mint(SESSION_SCOPE, user.id, session_id=row.id)
    return SessionCreateResponse(session_id=row.id, token=token, expires_at=expires_at)


@router.get("/sessions", response_model=SessionListResponse)
def list_sessions(
    db: Annotated[OrmSession, Depends(get_db)],
    claims: Annotated[TokenClaims, Depends(require_user_claims)],
) -> SessionListResponse:
    """List only the calling user's sessions."""
    rows = db.scalars(
        select(ChatSession)
        .where(ChatSession.user_id == int(claims["sub"]))
        .order_by(ChatSession.created_at)
    ).all()
    return SessionListResponse(sessions=[_to_out(row) for row in rows])


def _owned_session(db: OrmSession, claims: TokenClaims, session_id: str) -> ChatSession:
    if session_id_from(claims) != session_id:
        raise HTTPException(status_code=403, detail="Session token does not match.")
    row = db.get(ChatSession, session_id)
    if row is None or row.user_id != int(claims["sub"]):
        raise HTTPException(status_code=404, detail="Session not found.")
    return row


@router.patch("/session/{session_id}/name", response_model=SessionOut)
def rename_session(
    session_id: str,
    payload: RenameSessionRequest,
    db: Annotated[OrmSession, Depends(get_db)],
    claims: Annotated[TokenClaims, Depends(require_session_claims)],
) -> SessionOut:
    """Rename the session bound to the caller's session token."""
    row = _owned_session(db, claims, session_id)
    row.name = payload.name
    db.commit()
    db.refresh(row)
    return _to_out(row)


@router.delete("/session/{session_id}", response_model=DeleteSessionResponse)
def delete_session(
    session_id: str,
    db: Annotated[OrmSession, Depends(get_db)],
    claims: Annotated[TokenClaims, Depends(require_session_claims)],
) -> DeleteSessionResponse:
    """Delete the session bound to the caller's session token."""
    db.delete(_owned_session(db, claims, session_id))
    db.commit()
    return DeleteSessionResponse(session_id=session_id)
