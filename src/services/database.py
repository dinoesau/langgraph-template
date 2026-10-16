"""S2a persistence: lifespan-provided checkpointer plus store readiness.

The loop module never imports this service. The app enters the checkpointer
during lifespan, compiles the agent through the graph factory, and exposes
readiness through app state for health and route injection. S2b also builds
the memory service (pgvector with in-memory fallback) and the shared cache
(Valkey with in-memory fallback) on the same lifespan.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from src.agent.graph import graph, make_the_agent
from src.core.cache import InMemoryCache, ValkeyCache, make_cache
from src.core.config import get_settings
from src.services.memory import InMemoryMemoryStore, MemoryService, PostgresMemoryStore

logger = logging.getLogger(__name__)

STORE_NOT_CONFIGURED = "not_configured"
STORE_READY = "ready"
STORE_UNAVAILABLE = "unavailable"

CACHE_NOT_CONFIGURED = "not_configured"
CACHE_READY = "ready"
CACHE_UNAVAILABLE = "unavailable"


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


def cache_status(app: FastAPI) -> str:
    """Report cache readiness for the health endpoint."""
    status = getattr(app.state, "cache_status", CACHE_NOT_CONFIGURED)
    if isinstance(status, str) and status:
        return status
    return CACHE_NOT_CONFIGURED


def _build_cache() -> InMemoryCache | ValkeyCache:
    settings = get_settings()
    return make_cache(settings.VALKEY_HOST, settings.VALKEY_PORT, settings.CACHE_TTL_SECONDS)


def _cache_readiness(cache: InMemoryCache | ValkeyCache) -> str:
    if isinstance(cache, ValkeyCache):
        return CACHE_READY if cache.shared else CACHE_UNAVAILABLE
    if get_settings().VALKEY_HOST.strip():
        return CACHE_UNAVAILABLE
    return CACHE_NOT_CONFIGURED


async def _build_memory_service(cache: InMemoryCache | ValkeyCache) -> MemoryService:
    settings = get_settings()
    conn_string = settings.POSTGRES_URI.strip()
    if not conn_string:
        store: InMemoryMemoryStore | PostgresMemoryStore = InMemoryMemoryStore()
        return MemoryService(
            store=store,
            cache=cache,
            ttl_seconds=settings.CACHE_TTL_SECONDS,
            top_k=settings.LONG_TERM_MEMORY_TOP_K,
        )
    try:
        from src.services.memory import ensure_memory_schema

        await asyncio.to_thread(ensure_memory_schema, conn_string)
        store = PostgresMemoryStore(conn_string)
        # Probe once so boot fails over to the fallback before serving traffic.
        await asyncio.to_thread(store.search, "boot", "pre-warm", 1)
    except Exception:
        logger.warning("memory store unavailable, using in-memory fallback", exc_info=True)
        store = InMemoryMemoryStore()
    return MemoryService(
        store=store,
        cache=cache,
        ttl_seconds=settings.CACHE_TTL_SECONDS,
        top_k=settings.LONG_TERM_MEMORY_TOP_K,
    )


@asynccontextmanager
async def database_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Provide AsyncPostgresSaver via lifespan, degrading to stateless offline."""
    cache = _build_cache()
    app.state.memory_cache = cache
    app.state.cache_status = _cache_readiness(cache)
    app.state.memory_service = await _build_memory_service(cache)
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
