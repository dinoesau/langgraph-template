"""Persistence seam: checkpointer lifespan plus the Alembic ownership boundary."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from agent.graph import make_the_agent
from core.config import get_settings

# Tables managed outside Alembic: LangGraph owns checkpoints, memory owns vectors.
# shortcut: memory vector table joins this set when the memory slice lands.
EXCLUDED_TABLES: frozenset[str] = frozenset(
    {"checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations"}
)


def alembic_include_object(
    object: Any, name: str, type_: str, reflected: bool, compare_to: Any
) -> bool:
    """Keep Alembic away from framework tables so migrations never drop loop state."""
    return not (type_ == "table" and name in EXCLUDED_TABLES)


@asynccontextmanager
async def agent_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Attach a persisted loop when POSTGRES_URI is set, else leave the ephemeral graph."""
    postgres_uri = get_settings().postgres_uri
    if not postgres_uri:
        yield
        return
    async with AsyncPostgresSaver.from_conn_string(postgres_uri) as saver:
        await saver.setup()
        app.state.agent_graph = make_the_agent(checkpointer=saver)
        yield
