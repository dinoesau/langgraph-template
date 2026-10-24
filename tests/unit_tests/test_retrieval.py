"""Unit tests for S5a grounded retrieval.

No network and no containers: passages come from a seeded in-memory
store and replies come from a scripted fake llm.
"""

import asyncio
from typing import Any, cast

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import START, StateGraph

from agent.graph import graph
from agent.retrieval import (
    UNKNOWN,
    build_generate_async,
    build_generate_sync,
    build_retrieve_async,
    build_retrieve_sync,
)
from agent.state import GraphState
from tests.unit_tests.fakes import FakeLlm, fixed_state, make_store


def _make_demo(llm: FakeLlm, seed: bool) -> Any:
    builder = StateGraph(GraphState)
    builder.add_node("retrieve", build_retrieve_sync())
    builder.add_node("generate", build_generate_sync(llm))
    builder.add_edge(START, "retrieve")
    builder.add_edge("retrieve", "generate")
    return builder.compile(store=make_store(seed=seed))


class FailingLlm(FakeLlm):
    """Fake llm that raises on every call to prove the degrade path."""

    def _next(self) -> AIMessage:
        raise RuntimeError("fake llm failure")


def test_retrieve_fills_passages_for_policy_question() -> None:
    demo = _make_demo(FakeLlm(["Refunds within 30 days [1]"]), seed=True)
    result = demo.invoke(fixed_state("what is your refund policy"))
    passages = result.get("passages") or []
    assert len(passages) == 2
    assert any("30 days" in chunk for chunk in passages)


def test_generate_cites_passages_and_skips_unknown() -> None:
    demo = _make_demo(FakeLlm(["Refunds within 30 days [1]"]), seed=True)
    result = demo.invoke(fixed_state("what is your refund policy"))
    messages = result["messages"]
    assert isinstance(messages[-1], AIMessage)
    assert messages[-1].content == "Refunds within 30 days [1]"
    assert messages[-1].content != UNKNOWN


def test_generate_answers_unknown_when_store_empty() -> None:
    llm = FakeLlm(["should never be used"])
    demo = _make_demo(llm, seed=False)
    result = demo.invoke(fixed_state("what is the capital of France"))
    assert result["messages"][-1].content == UNKNOWN
    assert llm.calls == []


def test_generate_degrades_to_unknown_on_model_failure() -> None:
    llm = FailingLlm()
    demo = _make_demo(llm, seed=True)
    result = demo.invoke(fixed_state("what is your refund policy"))
    assert result["messages"][-1].content == UNKNOWN
    assert len(llm.calls) == 1


def test_grounded_prompt_delimits_passages_as_data() -> None:
    llm = FakeLlm(["cited [1]"])
    demo = _make_demo(llm, seed=True)
    demo.invoke(fixed_state("what is your refund policy"))
    system_text = str(llm.calls[0][0].content)
    assert "<passages>" in system_text
    assert "Refund" in system_text.split("<passages>")[1]
    demo = _make_demo(FakeLlm(["ok [1]"]), seed=True)
    result = demo.invoke({**fixed_state("refund policy"), "passages": ["stale"]})
    assert result.get("passages") != ["stale"]


def test_retrieve_degrades_to_echo_without_store() -> None:
    result = graph.invoke({"messages": [HumanMessage(content="hello")]})
    assert result["messages"][-1].content == "Echo: hello"
    assert result.get("passages") is None


@pytest.mark.parametrize("text", ["", "   "])
def test_generate_rejects_blank_input(text: str) -> None:
    generate = build_generate_sync(FakeLlm())
    with pytest.raises(ValueError):
        generate({"messages": [HumanMessage(content=text)], "passages": ["chunk"]})


async def _run_async_demo() -> Any:
    builder = StateGraph(GraphState)
    builder.add_node("retrieve", build_retrieve_async())
    builder.add_node("generate", build_generate_async(FakeLlm(["async cited [1]"])))
    builder.add_edge(START, "retrieve")
    builder.add_edge("retrieve", "generate")
    demo = builder.compile(store=make_store(seed=True))
    return await demo.ainvoke(cast(GraphState, fixed_state("refund policy")))


def test_async_retrieve_plus_generate() -> None:
    result = asyncio.run(_run_async_demo())
    assert result["messages"][-1].content == "async cited [1]"
