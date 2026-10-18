"""Persistence seam: checkpointer lifespan plus the Alembic ownership boundary."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from agent.graph import AgentGraph, graph, make_the_agent
from core.config import get_settings

# Tables managed outside Alembic: LangGraph owns checkpoints, memory owns vectors.
EXCLUDED_TABLES: frozenset[str] = frozenset(
    {
        "checkpoints",
        "checkpoint_blobs",
        "checkpoint_writes",
        "checkpoint_migrations",
        "memory_vectors",
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
    """Return the lifespan-persisted loop when present, else the ephemeral module graph."""
    return getattr(app.state, "agent_graph", None) or graph


@asynccontextmanager
async def agent_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Attach a persisted loop when POSTGRES_URI is set, else leave the ephemeral graph."""
    from services.memory import ensure_memory_schema, schedule_store, search

    postgres_uri = normalize_postgres_uri(get_settings().postgres_uri)
    if not postgres_uri:
        yield
        return
    async with AsyncPostgresSaver.from_conn_string(postgres_uri) as saver:
        await saver.setup()
        await ensure_memory_schema()
        app.state.agent_graph = make_the_agent(
            checkpointer=saver, recall_hook=search, store_hook=schedule_store
        )
        yield
