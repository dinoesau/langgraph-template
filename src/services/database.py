"""Persistence seam: checkpointer plus store lifespan plus the Alembic boundary."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore

from agent.graph import AgentGraph, graph, make_the_agent
from core.cache import RecallCache, build_cache
from core.config import get_settings

# Tables managed outside Alembic: LangGraph owns checkpoints, the store owns rows.
EXCLUDED_TABLES: frozenset[str] = frozenset(
    {
        "checkpoints",
        "checkpoint_blobs",
        "checkpoint_writes",
        "checkpoint_migrations",
        "store",
        "store_vectors",
        "store_migrations",
    }
)


def alembic_include_object(
    object: Any, name: str, type_: str, reflected: bool, compare_to: Any
) -> bool:
    """Keep Alembic away from framework tables so migrations never drop loop state."""
    return not (type_ == "table" and name in EXCLUDED_TABLES)


def normalize_postgres_uri(raw: str) -> str:
    """Strip whitespace and unify the scheme so app and migrations read one value."""
    uri = raw.strip()
    if uri.startswith("postgres://"):
        return "postgresql://" + uri[len("postgres://") :]
    return uri


def get_agent_graph(app: FastAPI) -> AgentGraph:
    """Return the lifespan-built loop when present, else the ephemeral module graph."""
    return getattr(app.state, "agent_graph", None) or graph


def get_agent_store(app: FastAPI) -> BaseStore | None:
    """Return the lifespan-built store when present, else None."""
    return getattr(app.state, "agent_store", None)


def get_recall_cache(app: FastAPI) -> RecallCache | None:
    """Return the lifespan-built cache when present, else None."""
    return getattr(app.state, "recall_cache", None)


def build_loop(
    checkpointer: Any, store: BaseStore, cache: RecallCache, cache_ttl_seconds: int
) -> AgentGraph:
    """Compile the loop with persistence plus recall wired from lifespan resources."""
    return make_the_agent(
        checkpointer=checkpointer, store=store, cache=cache, cache_ttl_seconds=cache_ttl_seconds
    )


@asynccontextmanager
async def agent_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Attach the loop with recall on every boot; Postgres upgrades durability.

    Without POSTGRES_URI the loop stays ephemeral on an in-memory store so
    fresh clones keep S2b recall. With it set, threads persist per thread
    identifier and vectors live in Postgres; startup still fails fast when
    the store is unreachable.
    """
    from langgraph.store.postgres.aio import AsyncPostgresStore

    settings = get_settings()
    postgres_uri = normalize_postgres_uri(settings.postgres_uri)
    cache = build_cache(settings.valkey_host)
    app.state.recall_cache = cache
    if not postgres_uri:
        store: BaseStore = InMemoryStore()
        app.state.agent_store = store
        app.state.agent_graph = build_loop(None, store, cache, settings.cache_ttl_seconds)
        yield
        return
    async with AsyncPostgresSaver.from_conn_string(postgres_uri) as saver:
        await saver.setup()
        async with AsyncPostgresStore.from_conn_string(
            postgres_uri, pool_config={"max_size": 5}
        ) as pg_store:
            await pg_store.setup()
            app.state.agent_store = pg_store
            app.state.agent_graph = build_loop(saver, pg_store, cache, settings.cache_ttl_seconds)
            yield
