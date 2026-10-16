"""S2a persistence: lifespan-provided checkpointer plus store readiness.

The loop module never imports this service. The app enters the checkpointer
during lifespan, compiles the agent through the graph factory, and exposes
readiness through app state for health and route injection.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from src.agent.graph import graph, make_the_agent
from src.core.config import get_settings

logger = logging.getLogger(__name__)

STORE_NOT_CONFIGURED = "not_configured"
STORE_READY = "ready"
STORE_UNAVAILABLE = "unavailable"


def store_status(app: FastAPI) -> str:
    """Report checkpointer readiness for the health endpoint."""
    status = getattr(app.state, "store_status", STORE_NOT_CONFIGURED)
    if isinstance(status, str) and status:
        return status
    return STORE_NOT_CONFIGURED


def get_agent_graph(app: FastAPI) -> Any:
    """Return the lifespan-compiled graph, else the stateless module graph."""
    candidate = getattr(app.state, "agent_graph", None)
    if candidate is not None:
        return candidate
    return graph


@asynccontextmanager
async def database_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Provide AsyncPostgresSaver via lifespan, degrading to stateless offline."""
    conn_string = get_settings().POSTGRES_URI.strip()
    if not conn_string:
        app.state.checkpointer = None
        app.state.agent_graph = None
        app.state.store_status = STORE_NOT_CONFIGURED
        yield
        return
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        async with AsyncPostgresSaver.from_conn_string(conn_string) as saver:
            await saver.setup()
            app.state.checkpointer = saver
            app.state.agent_graph = make_the_agent(checkpointer=saver)
            app.state.store_status = STORE_READY
            yield
    except Exception:
        logger.warning("postgres checkpointer unavailable, running stateless", exc_info=True)
        app.state.checkpointer = None
        app.state.agent_graph = None
        app.state.store_status = STORE_UNAVAILABLE
        yield
