"""Request-scoped dependencies: thin readers over process resources."""

from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, NoReturn

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer, SecurityScopes
from langchain_core.messages import BaseMessage
from sqlalchemy.ext.asyncio import AsyncSession

from agent.graph import AgentGraph
from core.config import get_settings
from core.memory import valid_user_id
from services.auth_security import InvalidTokenError, decode_token
from services.auth_store import get_session, get_user_by_id
from services.database import get_agent_graph, get_agent_store
from services.memory import persist_turn

WriterHook = Callable[[str, list[BaseMessage]], Awaitable[None]]

oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/api/v1/auth/login",
    scopes={"user": "Manage sessions", "session": "Chat in one session"},
)


@dataclass(frozen=True)
class SessionContext:
    """Identity for one request, primitives only."""

    user_id: str
    session_id: str | None = None


async def get_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """Yield one auth session from the lifespan factory."""
    factory = getattr(request.app.state, "auth_session_factory", None)
    if factory is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Auth store is not configured",
        )
    async with factory() as session:
        yield session


def get_request_graph(request: Request) -> AgentGraph:
    """Resolve the loop for this request from what the lifespan attached."""
    return get_agent_graph(request.app)


def get_request_user_id(request: Request) -> str:
    """Read the transitional caller id; empty means no recall for the turn.

    Session tokens replace this header once the auth layer lands. Production ignores
    the header so unauthenticated clients cannot spoof recall; non-production
    honors it for local demos. The value is stripped and length-capped so logs
    and cache keys stay small.
    """
    if get_settings().app_env == "production":
        return ""
    return valid_user_id(request.headers.get("X-User-ID", ""))


def get_memory_writer(request: Request) -> WriterHook:
    """Return the background writer bound to the lifespan store for this app."""

    async def _write(user_id: str, messages: list[BaseMessage]) -> None:
        store = get_agent_store(request.app)
        if store is None or not user_id.strip():
            return
        await persist_turn(store, user_id, messages)

    return _write


def _auth_challenge(scope: str = "") -> dict[str, str]:
    """Build the WWW-Authenticate header for 401 responses (RFC 9110 s11.4)."""
    value = 'Bearer realm="auth"'
    if scope:
        value += f' scope="{scope}"'
    return {"WWW-Authenticate": value}


def _unauthorized(scope_str: str) -> NoReturn:
    """Raise 401 with a scope-echo challenge."""
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers=_auth_challenge(scope_str),
    )


def require_scope(required: str) -> Callable[..., Awaitable[SessionContext]]:
    """Return a dependency enforcing one OAuth2 scope plus sid binding."""

    async def _guard(
        security_scopes: SecurityScopes,
        token: Annotated[str, Depends(oauth2_scheme)],
        db: Annotated[AsyncSession, Depends(get_db)],
    ) -> SessionContext:
        settings = get_settings()
        try:
            data = decode_token(
                token,
                secret=settings.jwt_secret_key,
                algorithms=[settings.jwt_algorithm],
            )
        except InvalidTokenError:
            _unauthorized(security_scopes.scope_str)
        if required not in data.scopes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not enough permissions",
            )
        try:
            user_id = int(data.sub)
        except ValueError:
            _unauthorized(security_scopes.scope_str)
        if required == "user":
            if await get_user_by_id(db, user_id) is None:
                _unauthorized(security_scopes.scope_str)
            return SessionContext(user_id=data.sub)
        if not data.sid:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Session token is missing its session",
            )
        session = await get_session(db, data.sid)
        if session is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Session not found",
            )
        if session.user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Session scope mismatch",
            )
        return SessionContext(user_id=data.sub, session_id=session.id)

    return _guard
