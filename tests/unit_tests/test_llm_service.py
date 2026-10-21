"""Unit tests for the S3 model fallback plus tool loop.

No network and no containers: raw models are duck-typed fakes, the loop
uses a scripted llm double, tools are the real booking tools.
"""

import asyncio
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agent.graph import make_the_agent
from agent.routes import should_continue
from agent.state import GraphState
from agent.tools import tools, tools_by_name
from core.config import get_settings
from services.llm.registry import build_registry, ordered_registry
from services.llm.service import LlmService, is_retryable


class FakeRawModel:
    """Duck-typed chat model: script of messages or exceptions in order."""

    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.calls = 0
        self.bound: Any = None

    def bind_tools(self, tool_list: Any, **kwargs: Any) -> "FakeRawModel":
        self.bound = tool_list
        return self

    def _next(self) -> Any:
        self.calls += 1
        item = self.script[min(self.calls - 1, len(self.script) - 1)]
        if isinstance(item, Exception):
            raise item
        return item

    def invoke(self, messages: Any) -> Any:
        return self._next()

    async def ainvoke(self, messages: Any) -> Any:
        return self._next()


class FakeLlm:
    """Duck-typed llm service double for the loop tests."""

    def __init__(self, script: list[AIMessage]) -> None:
        self.script = list(script)
        self.calls = 0
        self.seen_tools: list[Any] = []

    def call(self, messages: Any, tool_list: Any = None, **kwargs: Any) -> AIMessage:
        self.calls += 1
        self.seen_tools.append(tool_list)
        return self.script[min(self.calls - 1, len(self.script) - 1)]

    async def acall(self, messages: Any, tool_list: Any = None, **kwargs: Any) -> AIMessage:
        return self.call(messages, tool_list, **kwargs)


def _msg(text: str) -> list[Any]:
    return [HumanMessage(content=text)]


def test_registry_skips_missing_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("GOOGLE_API_KEY", "")
    get_settings.cache_clear()
    assert build_registry(get_settings()) == []
    get_settings.cache_clear()


def test_ordered_registry_walks_circularly_once() -> None:
    fake = [("a", object()), ("b", object()), ("c", object())]
    entries = ordered_registryf(fake, "b")
    assert [name for name, _ in entries] == ["b", "c", "a"]


def _none_model() -> Any:
    raise AssertionError("never built")


def ordered_registryf(models: list[tuple[str, object]], start: str) -> list[tuple[str, object]]:
    names = [name for name, _ in models]
    index = names.index(start)
    return models[index:] + models[:index]


def test_ordered_registry_helper_matches_service() -> None:
    from services.llm.registry import ModelEntry

    entries = [ModelEntry(name=n, build=_none_model) for n in ("a", "b", "c")]
    assert [e.name for e in ordered_registry(entries, "b")] == ["b", "c", "a"]
    assert [e.name for e in ordered_registry(entries, "missing")] == ["a", "b", "c"]


def test_retryable_classification() -> None:
    assert is_retryable(RuntimeError("rate limit 429")) is True
    assert is_retryable(TimeoutError("timed out")) is True
    assert is_retryable(RuntimeError("bad api key unauthorized")) is False
    assert is_retryable(ValueError("validation failed")) is False


def test_fallback_walks_registry_and_keeps_tools() -> None:
    first = FakeRawModel([RuntimeError("rate limit 429")] * 5)
    second = FakeRawModel([AIMessage(content="ok")])
    service = LlmService(
        [("m1", first), ("m2", second)],
        start="m1",
        max_retries=3,
        total_timeout_seconds=60,
        backoff_min_seconds=0,
        backoff_max_seconds=0,
    )
    result = service.call(_msg("hi"), tools=[object()])
    assert isinstance(result, AIMessage)
    assert result.content == "ok"
    assert first.calls == 3
    assert second.calls == 1
    assert second.bound is not None


def test_non_retryable_fails_fast_with_one_attempt() -> None:
    first = FakeRawModel([RuntimeError("invalid api key unauthorized")])
    second = FakeRawModel([AIMessage(content="ok")])
    service = LlmService(
        [("m1", first), ("m2", second)],
        max_retries=3,
        total_timeout_seconds=60,
        backoff_min_seconds=0,
        backoff_max_seconds=0,
    )
    with pytest.raises(RuntimeError, match="unauthorized"):
        service.call(_msg("hi"))
    assert first.calls == 1
    assert second.calls == 0


def test_registry_exhausted_raises_clear_error() -> None:
    models: list[tuple[str, Any]] = [
        ("m1", FakeRawModel([RuntimeError("503 overloaded")])),
        ("m2", FakeRawModel([RuntimeError("503 overloaded")])),
    ]
    service = LlmService(
        models,
        max_retries=2,
        total_timeout_seconds=60,
        backoff_min_seconds=0,
        backoff_max_seconds=0,
    )
    with pytest.raises(RuntimeError, match="MODEL_UNAVAILABLE"):
        service.call(_msg("hi"))


def test_budget_preempts_backoff() -> None:
    import time

    models: list[tuple[str, Any]] = [("m1", FakeRawModel([RuntimeError("503 busy")]))]
    service = LlmService(
        models,
        max_retries=5,
        total_timeout_seconds=1,
        backoff_min_seconds=5,
        backoff_max_seconds=10,
    )
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="MODEL_UNAVAILABLE"):
        service.call(_msg("hi"))
    assert time.monotonic() - started < 4


def test_async_parity() -> None:
    async def _run() -> Any:
        service = LlmService(
            [("m1", FakeRawModel([AIMessage(content="async-ok")]))],
            backoff_min_seconds=0,
            backoff_max_seconds=0,
        )
        return await service.acall(_msg("hi"))

    assert asyncio.run(_run()).content == "async-ok"


def test_tools_list_shape() -> None:
    assert [getattr(t, "name", "") for t in tools] == [
        "get_availability",
        "book_appointment",
        "get_order_status",
    ]
    assert set(tools_by_name) == {"get_availability", "book_appointment", "get_order_status"}


def test_should_continue_routes_on_tool_calls() -> None:
    tool_msg = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "get_availability",
                "args": {"date": "tomorrow"},
                "id": "1",
                "type": "tool_call",
            }
        ],
    )
    state: GraphState = {"messages": [HumanMessage(content="hi"), tool_msg]}
    assert should_continue(state) == "tools"
    plain: GraphState = {"messages": [HumanMessage(content="hi")]}
    assert should_continue(plain) == "__end__"


def test_time_request_converges_to_confirmation() -> None:
    async def _run() -> Any:
        llm = FakeLlm(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "get_availability",
                            "args": {"date": "tomorrow"},
                            "id": "c1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "book_appointment",
                            "args": {"slot": "09:00"},
                            "id": "c2",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="Booked 09:00 with confirmation BK-1234"),
            ]
        )
        agent = make_the_agent(llm=llm)
        return await agent.ainvoke(
            {"messages": [HumanMessage(content="book a time slot tomorrow")]},
            config={"configurable": {"thread_id": "slot-1"}},
        )

    result = asyncio.run(_run())
    assert result["messages"][-1].content == "Booked 09:00 with confirmation BK-1234"
    assert not getattr(result["messages"][-1], "tool_calls", [])


def test_tool_loop_skips_tools_when_no_calls() -> None:
    async def _run() -> Any:
        llm = FakeLlm([AIMessage(content="Echo: hi")])
        agent = make_the_agent(llm=llm)
        return await agent.ainvoke(
            {"messages": [HumanMessage(content="hi")]},
            config={"configurable": {"thread_id": "plain-1"}},
        )

    result = asyncio.run(_run())
    assert result["messages"][-1].content == "Echo: hi"
