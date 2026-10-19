"""Request-scoped dependencies: thin readers over process resources."""

from collections.abc import Awaitable, Callable

from fastapi import Request
from langchain_core.messages import BaseMessage

from agent.graph import AgentGraph
from core.config import get_settings
from core.memory import valid_user_id
from services.database import get_agent_graph, get_agent_store
from services.memory import persist_turn

WriterHook = Callable[[str, list[BaseMessage]], Awaitable[None]]


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
