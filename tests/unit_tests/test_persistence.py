import asyncio
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver

from src.agent.graph import graph, make_the_agent
from src.services.llm.service import llm_service


@pytest.fixture(autouse=True)
def _fake_llm_echo(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep persistence tests hermetic: the loop answers through a fake echo."""

    async def _echo(messages: Any, **kwargs: Any) -> AIMessage:
        text = ""
        assert isinstance(messages, list)
        for message in reversed(messages):
            if isinstance(message, HumanMessage):
                text = message.content if isinstance(message.content, str) else ""
                break
        return AIMessage(content=text)

    monkeypatch.setattr(llm_service, "call", _echo)


def _config(thread_id: str) -> RunnableConfig:
    return {"configurable": {"thread_id": thread_id}}


def _contents(result: object) -> list[str]:
    assert isinstance(result, dict)
    messages = result["messages"]
    return [str(message.content) for message in messages]


def test_factory_resumes_same_thread() -> None:
    agent = make_the_agent(checkpointer=InMemorySaver())
    config = _config("thread-resume-1")
    asyncio.run(agent.ainvoke({"messages": [HumanMessage(content="hello")]}, config=config))
    resumed = asyncio.run(
        agent.ainvoke({"messages": [HumanMessage(content="again")]}, config=config)
    )
    assert _contents(resumed) == ["hello", "hello", "again", "again"]


def test_factory_isolates_fresh_thread() -> None:
    agent = make_the_agent(checkpointer=InMemorySaver())
    first = asyncio.run(
        agent.ainvoke(
            {"messages": [HumanMessage(content="hello")]},
            config=_config("thread-a"),
        )
    )
    second = asyncio.run(
        agent.ainvoke(
            {"messages": [HumanMessage(content="hello")]},
            config=_config("thread-b"),
        )
    )
    assert _contents(first) == _contents(second) == ["hello", "hello"]


def test_factory_without_checkpointer_stays_stateless() -> None:
    agent = make_the_agent(checkpointer=None)
    config = _config("thread-stateless")
    asyncio.run(agent.ainvoke({"messages": [HumanMessage(content="hello")]}, config=config))
    resumed = asyncio.run(
        agent.ainvoke({"messages": [HumanMessage(content="again")]}, config=config)
    )
    assert _contents(resumed) == ["again", "again"]


def test_default_graph_export_stays_stateless() -> None:
    result = asyncio.run(graph.ainvoke({"messages": [HumanMessage(content="hello")]}))
    assert _contents(result) == ["hello", "hello"]


def test_loop_never_imports_storage() -> None:
    source = Path("src/agent/graph.py").read_text(encoding="utf-8").lower()
    assert "postgres" not in source
    assert "services" not in source
    assert "sqlalchemy" not in source
    assert "alembic" not in source
