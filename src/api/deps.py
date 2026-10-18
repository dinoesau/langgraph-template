"""Request-scoped dependencies: thin readers over process resources."""

from fastapi import Request

from agent.graph import AgentGraph
from core.config import get_settings
from services.database import get_agent_graph

MAX_USER_ID_CHARS = 128


def get_request_graph(request: Request) -> AgentGraph:
    """Resolve the loop for this request from what the lifespan attached."""
    return get_agent_graph(request.app)


def get_request_user_id(request: Request) -> str:
    """Read the transitional caller id; empty means no recall for the turn.

    S4 replaces this header with the session token claim. Production ignores
    the header so unauthenticated clients cannot spoof recall; non-production
    honors it for S2b demos. The value is stripped and length-capped so logs
    and cache keys stay small.
    """
    if get_settings().app_env == "production":
        return ""
    raw = request.headers.get("X-User-ID", "")
    clean = " ".join(raw.strip().split())
    return clean[:MAX_USER_ID_CHARS]
