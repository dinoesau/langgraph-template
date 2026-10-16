"""Slice 2 seam: LLMService.call retries, falls back, and guards the budget."""

import asyncio
from collections.abc import Sequence
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda
from pydantic import BaseModel, Field

from src.services.llm.service import LLMService


class RateLimitError(Exception):
    """Stand-in for provider rate-limit errors."""


class APITimeoutError(Exception):
    """Stand-in for provider timeout errors."""


class Ticket(BaseModel):
    reply: str


class ScriptedChatModel(BaseChatModel):
    """Fake chat model playing a script of messages or errors."""

    outcomes: list[Any] = Field(default_factory=list)
    attempts: int = 0
    bind_calls: int = 0
    structured_calls: int = 0
    structured_result: Any = None

    @property
    def _llm_type(self) -> str:
        return "scripted-fake"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.attempts += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        assert isinstance(outcome, BaseMessage)
        return ChatResult(generations=[ChatGeneration(message=outcome)])

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> Any:
        self.bind_calls += 1
        model = self

        async def _run(messages: Any) -> Any:
            return await model.ainvoke(messages)

        return RunnableLambda(_run)

    def with_structured_output(self, schema: Any, **kwargs: Any) -> Any:
        self.structured_calls += 1
        model = self

        def _run(_messages: Any) -> Any:
            outcome = model.outcomes.pop(0)
            model.attempts += 1
            if isinstance(outcome, Exception):
                raise outcome
            return model.structured_result

        return RunnableLambda(_run)


def _ok(text: str = "ok") -> AIMessage:
    return AIMessage(content=text)


class _Sleeper:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_retry_then_success_with_bounded_backoff() -> None:
    model = ScriptedChatModel(outcomes=[RateLimitError(), APITimeoutError(), _ok()])
    sleep = _Sleeper()
    service = LLMService(models={"fake-a": model}, sleep=sleep.__call__)
    result = _run(service.call([HumanMessage(content="hi")]))
    assert isinstance(result, AIMessage)
    assert result.content == "ok"
    assert model.attempts == 3
    assert sleep.delays == [2.0, 4.0]


def test_backoff_stays_within_bounds() -> None:
    model = ScriptedChatModel(outcomes=[RateLimitError()] * 10)
    sleep = _Sleeper()
    service = LLMService(models={"fake-a": model}, max_retries=10, sleep=sleep.__call__)
    with pytest.raises(RuntimeError):
        _run(service.call([HumanMessage(content="hi")]))
    assert sleep.delays == [2.0, 4.0, 8.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0]


def test_non_retryable_fails_fast() -> None:
    model = ScriptedChatModel(outcomes=[ValueError("bad request"), _ok()])
    sleep = _Sleeper()
    service = LLMService(models={"fake-a": model}, sleep=sleep.__call__)
    with pytest.raises(ValueError, match="bad request"):
        _run(service.call([HumanMessage(content="hi")]))
    assert model.attempts == 1
    assert sleep.delays == []


def test_fallback_switch_after_exhausted_retries() -> None:
    failing = ScriptedChatModel(outcomes=[RateLimitError(), RateLimitError(), RateLimitError()])
    working = ScriptedChatModel(outcomes=[_ok("from-b")])
    service = LLMService(
        models={"fake-a": failing, "fake-b": working},
        sleep=_Sleeper().__call__,
    )
    result = _run(service.call([HumanMessage(content="hi")]))
    assert isinstance(result, AIMessage)
    assert result.content == "from-b"
    assert failing.attempts == 3
    assert working.attempts == 1


def test_circular_start_offset_wraps_around() -> None:
    a = ScriptedChatModel(outcomes=[_ok("from-a")])
    b = ScriptedChatModel(outcomes=[_ok("from-b")])
    c = ScriptedChatModel(
        outcomes=[RateLimitError(), RateLimitError(), RateLimitError(), _ok("late")]
    )
    service = LLMService(models={"fake-a": a, "fake-b": b, "fake-c": c}, sleep=_Sleeper().__call__)
    result = _run(service.call([HumanMessage(content="hi")], model_name="fake-c"))
    assert isinstance(result, AIMessage)
    assert result.content == "from-a"
    assert c.attempts == 3
    assert b.attempts == 0


def test_unknown_start_model_uses_registry_head() -> None:
    a = ScriptedChatModel(outcomes=[_ok("from-a")])
    service = LLMService(models={"fake-a": a}, sleep=_Sleeper().__call__)
    result = _run(service.call([HumanMessage(content="hi")], model_name="nope"))
    assert isinstance(result, AIMessage)
    assert result.content == "from-a"


def test_skips_unbuildable_model() -> None:
    def _boom() -> BaseChatModel:
        raise RuntimeError("missing key")

    working = ScriptedChatModel(outcomes=[_ok("built")])
    service = LLMService(
        models={"broken": _boom, "fake-b": working},
        sleep=_Sleeper().__call__,
    )
    result = _run(service.call([HumanMessage(content="hi")]))
    assert isinstance(result, AIMessage)
    assert result.content == "built"


def test_all_fail_raises_runtime_error() -> None:
    a = ScriptedChatModel(outcomes=[RateLimitError()] * 3)
    b = ScriptedChatModel(outcomes=[APITimeoutError()] * 3)
    service = LLMService(models={"fake-a": a, "fake-b": b}, sleep=_Sleeper().__call__)
    with pytest.raises(RuntimeError, match="all models failed"):
        _run(service.call([HumanMessage(content="hi")]))


def test_total_timeout_guard() -> None:
    import time

    class Slow(BaseChatModel):
        @property
        def _llm_type(self) -> str:
            return "slow-fake"

        def _generate(
            self,
            messages: list[BaseMessage],
            stop: list[str] | None = None,
            run_manager: Any = None,
            **kwargs: Any,
        ) -> ChatResult:
            time.sleep(2)
            return ChatResult(generations=[ChatGeneration(message=_ok())])

    service = LLMService(models={"slow": Slow()}, total_timeout=0.05)
    with pytest.raises(RuntimeError, match="total timeout"):
        _run(service.call([HumanMessage(content="hi")]))


def test_structured_output_chains_on_fallback() -> None:
    failing = ScriptedChatModel(outcomes=[RateLimitError()] * 3)
    ticket = Ticket(reply="booked 10:00")
    working = ScriptedChatModel(outcomes=[_ok()], structured_result=ticket)
    service = LLMService(models={"fake-a": failing, "fake-b": working}, sleep=_Sleeper().__call__)
    result = _run(service.call([HumanMessage(content="hi")], response_format=Ticket))
    assert result == ticket
    assert working.structured_calls == 1


def test_tools_rebound_on_fallback() -> None:
    from src.agent.tools import tools

    failing = ScriptedChatModel(outcomes=[RateLimitError()] * 3)
    working = ScriptedChatModel(outcomes=[_ok("done")])
    service = LLMService(models={"fake-a": failing, "fake-b": working}, sleep=_Sleeper().__call__)
    service.bind_tools(tools)
    result = _run(service.call([HumanMessage(content="hi")]))
    assert isinstance(result, AIMessage)
    assert result.content == "done"
    assert failing.bind_calls == 1
    assert working.bind_calls == 1
