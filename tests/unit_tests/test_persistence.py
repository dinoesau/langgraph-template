"""Unit tests for persisted threads plus the migration boundary.

No network and no containers: threads persist on an in-memory saver,
the migration boundary is a pure function test.
"""

from pathlib import Path

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver

from agent.graph import graph, make_the_agent
from services.database import EXCLUDED_TABLES, alembic_include_object, normalize_postgres_uri


def test_ephemeral_graph_stays_stateless() -> None:
    first = graph.invoke({"messages": [HumanMessage(content="one")]})
    second = graph.invoke({"messages": [HumanMessage(content="two")]})
    assert len(first["messages"]) == 2
    assert len(second["messages"]) == 2


def test_same_thread_resumes_across_recompiles() -> None:
    saver = MemorySaver()
    config: RunnableConfig = {"configurable": {"thread_id": "thread-1"}}
    first = make_the_agent(checkpointer=saver)
    first.invoke({"messages": [HumanMessage(content="one")]}, config=config)
    # Recompile on the same saver to simulate a service restart.
    second = make_the_agent(checkpointer=saver)
    result = second.invoke({"messages": [HumanMessage(content="two")]}, config=config)
    assert [m.content for m in result["messages"]] == ["one", "Echo: one", "two", "Echo: two"]


def test_threads_stay_isolated() -> None:
    agent = make_the_agent(checkpointer=MemorySaver())
    config_a: RunnableConfig = {"configurable": {"thread_id": "a"}}
    config_b: RunnableConfig = {"configurable": {"thread_id": "b"}}
    agent.invoke({"messages": [HumanMessage(content="one")]}, config=config_a)
    result = agent.invoke({"messages": [HumanMessage(content="two")]}, config=config_b)
    assert [m.content for m in result["messages"]] == ["two", "Echo: two"]


def test_loop_never_imports_storage() -> None:
    source = Path("src/agent/graph.py").read_text()
    for forbidden in ("services", "postgres", "psycopg", "sqlalchemy", "alembic"):
        assert forbidden not in source


def test_migrations_exclude_framework_tables() -> None:
    assert EXCLUDED_TABLES == {
        "checkpoints",
        "checkpoint_blobs",
        "checkpoint_writes",
        "checkpoint_migrations",
    }
    for table in EXCLUDED_TABLES:
        assert alembic_include_object(None, table, "table", False, None) is False
    for table in ("users", "sessions"):
        assert alembic_include_object(None, table, "table", False, None) is True


def test_normalize_postgres_uri() -> None:
    assert normalize_postgres_uri("") == ""
    assert normalize_postgres_uri("   ") == ""
    assert (
        normalize_postgres_uri("postgresql://postgres:postgres@localhost:5432/postgres")
        == "postgresql://postgres:postgres@localhost:5432/postgres"
    )
    assert (
        normalize_postgres_uri("postgres://postgres:postgres@localhost:5432/postgres")
        == "postgresql://postgres:postgres@localhost:5432/postgres"
    )
