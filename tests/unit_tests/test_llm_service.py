"""Unit tests for the S3 model fallback plus tool loop.

No network and no containers: raw models are duck-typed fakes, the loop
uses a scripted llm double, tools are the real booking tools.
"""

import asyncio
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel

from agent.graph import make_the_agent
from agent.routes import should_continue
from agent.state import GraphState
from agent.tools import tools
from core.config import get_settings
from services.llm.registry import ModelEntry, build_registry, ordered_registry, rotate
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


class FakeStructuredModel(FakeRawModel):
    """Fake that records every structured-output chain for rebind asserts."""

    def __init__(self, script: list[Any]) -> None:
        super().__init__(script)
        self.structured_schemas: list[Any] = []

    def with_structured_output(self, schema: Any, **kwargs: Any) -> "FakeStructuredModel":
        self.structured_schemas.append(schema)
        return self


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


class ReplySchema(BaseModel):
    """Terminal structured shape used to prove chaining across retries."""

    text: str


def _msg(text: str) -> list[Any]:
    return [HumanMessage(content=text)]


def _zero_backoff_service(models: list[tuple[str, Any]], **kwargs: Any) -> LlmService:
    """Service double with no sleeps so retry tests stay instant."""
    return LlmService(
        models,
        max_retries=3,
        total_timeout_seconds=60,
        backoff_min_seconds=0,
        backoff_max_seconds=0,
        **kwargs,
    )


def _registry_names(names: list[str]) -> list[ModelEntry]:
    def _never() -> Any:
        raise AssertionError("factory must not run in a routing test")

    return [ModelEntry(name=name, build=_never) for name in names]


def test_registry_skips_missing_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("GOOGLE_API_KEY", "")
    get_settings.cache_clear()
    assert build_registry(get_settings()) == []
    get_settings.cache_clear()


@pytest.mark.parametrize(
    ("start", "expected"),
    [
        ("b", ["b", "c", "a"]),
        ("missing", ["a", "b", "c"]),
        ("a", ["a", "b", "c"]),
    ],
)
def test_ordered_registry_walks_circularly_once(start: str, expected: list[str]) -> None:
    assert [e.name for e in ordered_registry(_registry_names(["a", "b", "c"]), start)] == expected
    assert ordered_registry([], start) == []
    assert rotate([1, 2, 3], 4) == [2, 3, 1]


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (RuntimeError("rate limit 429"), True),
        (TimeoutError("timed out"), True),
        (ConnectionError("connection reset"), True),
        (RuntimeError("503 overloaded"), True),
        (RuntimeError("bad api key unauthorized"), False),
        (ValueError("validation failed"), False),
        (RuntimeError("permission denied"), False),
        # Pinned choice: unknown shapes retry into the fallback chain.
        (RuntimeError("boom"), True),
    ],
)
def test_retryable_classification(error: Exception, expected: bool) -> None:
    assert is_retryable(error) is expected


def test_fallback_walks_registry_and_rebinds_real_tools() -> None:
    first = FakeRawModel([RuntimeError("rate limit 429")] * 5)
    second = FakeRawModel([AIMessage(content="ok")])
    service = _zero_backoff_service([("m1", first), ("m2", second)], start="m1")
    result = service.call(_msg("hi"), tools=tools)
    assert isinstance(result, AIMessage)
    assert result.content == "ok"
    assert first.calls == 3
    assert second.calls == 1
    assert second.bound == tools


def test_non_retryable_fails_fast_with_one_attempt() -> None:
    first = FakeRawModel([RuntimeError("invalid api key unauthorized")])
    second = FakeRawModel([AIMessage(content="ok")])
    service = _zero_backoff_service([("m1", first), ("m2", second)])
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


def test_structured_output_chain_reapplies_on_retry_and_fallback() -> None:
    first = FakeStructuredModel([RuntimeError("rate limit 429"), ReplySchema(text="x")])
    service = _zero_backoff_service([("m1", first)])
    result = service.call(_msg("hi"), response_format=ReplySchema)
    assert result == ReplySchema(text="x")
    assert first.structured_schemas == [ReplySchema, ReplySchema]

    failing = FakeStructuredModel([RuntimeError("503 busy")] * 4)
    following = FakeStructuredModel([ReplySchema(text="y")])
    chained = _zero_backoff_service([("m1", failing), ("m2", following)])
    assert chained.call(_msg("hi"), response_format=ReplySchema) == ReplySchema(text="y")
    assert following.structured_schemas == [ReplySchema]


def test_async_fallback_parity() -> None:
    async def _run() -> Any:
        first = FakeRawModel([RuntimeError("rate limit 429")] * 5)
        second = FakeRawModel([AIMessage(content="async-ok")])
        service = _zero_backoff_service([("m1", first), ("m2", second)], start="m1")
        return await service.acall(_msg("hi"), tools)

    async def _plain() -> Any:
        service = _zero_backoff_service([("m1", FakeRawModel([AIMessage(content="async-ok")]))])
        return await service.acall(_msg("hi"))

    assert asyncio.run(_run()).content == "async-ok"
    assert asyncio.run(_plain()).content == "async-ok"


def test_tools_list_shape() -> None:
    assert [getattr(item, "name", "") for item in tools] == [
        "get_availability",
        "book_appointment",
    ]


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


def _tool_call(name: str, args: dict[str, str], call_id: str) -> dict[str, Any]:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def test_time_request_converges_to_confirmation() -> None:
    async def _run() -> Any:
        llm = FakeLlm(
            [
                AIMessage(
                    content="",
                    tool_calls=[_tool_call("get_availability", {"date": "tomorrow"}, "c1")],
                ),
                AIMessage(
                    content="",
                    tool_calls=[_tool_call("book_appointment", {"slot": "09:00"}, "c2")],
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


def test_parallel_tool_calls_converge_in_one_turn() -> None:
    async def _run() -> Any:
        llm = FakeLlm(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        _tool_call("get_availability", {"date": "tomorrow"}, "p1"),
                        _tool_call("book_appointment", {"slot": "09:00"}, "p2"),
                    ],
                ),
                AIMessage(content="Booked 09:00 with confirmation BK-1234"),
            ]
        )
        agent = make_the_agent(llm=llm)
        return await agent.ainvoke(
            {"messages": [HumanMessage(content="book tomorrow 09:00")]},
            config={"configurable": {"thread_id": "parallel-1"}},
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
