"""Shared API dependencies: scoped JWT guards and the auth DB session seam.

Chat validates session tokens by signature only and binds the thread to the
token ``sid``; session CRUD additionally checks the ORM row for ownership.
"""

from collections.abc import Callable, Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from src.core.config import get_settings
from src.core.security import SESSION_SCOPE, USER_SCOPE, AuthError, TokenClaims, decode_token

_bearer_scheme = HTTPBearer(auto_error=False)

SessionFactory = Callable[[], OrmSession]

_default_engine: Engine | None = None


def _sync_url(url: str) -> str:
    """Map the async-style Postgres URL to its sync SQLAlchemy driver."""
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def default_session_factory() -> OrmSession:
    """Open an ORM session on the configured Postgres; tests override this."""
    global _default_engine
    if _default_engine is None:
        _default_engine = create_engine(_sync_url(get_settings().POSTGRES_URI))
    return sessionmaker(bind=_default_engine)()


def get_db(request: Request) -> Iterator[OrmSession]:
    """Yield one ORM session; prefers ``app.state.auth_session_factory``."""
    factory = getattr(request.app.state, "auth_session_factory", None)
    maker: SessionFactory = factory if factory is not None else default_session_factory
    db = maker()
    try:
        yield db
    finally:
        db.close()


def _claims_for(
    creds: HTTPAuthorizationCredentials | None, scope: str, forbidden_detail: str
) -> TokenClaims:
    if creds is None or not creds.credentials:
        raise HTTPException(status_code=401, detail="Missing bearer token.")
    try:
        claims = decode_token(creds.credentials, get_settings().JWT_SECRET_KEY)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    if claims["scope"] != scope:
        raise HTTPException(status_code=403, detail=forbidden_detail)
    return claims


def require_user_claims(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
) -> TokenClaims:
    """Gate session create/list on a user-scoped token."""
    return _claims_for(creds, USER_SCOPE, "A user token is required here.")


def require_session_claims(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
) -> TokenClaims:
    """Gate chat/rename/delete on a session-scoped token."""
    return _claims_for(creds, SESSION_SCOPE, "A session token is required here.")


def session_id_from(claims: TokenClaims) -> str:
    """Return the bound session id or reject a session token without one."""
    sid = claims.get("sid")
    if not sid:
        raise HTTPException(status_code=403, detail="Session token has no session.")
    return sid
