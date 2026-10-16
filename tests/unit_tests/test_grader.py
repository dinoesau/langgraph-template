"""Slice S5d seam: generator drafts, grader checks, guard accepts last."""

import asyncio
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from src.agent.graph import make_evaluator_agent
from src.agent.nodes import GradeDecision, generator, grader
from src.agent.routes import route_after_grader
from src.agent.state import GraphState
from src.services.llm.service import llm_service


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_generator_drafts_via_llm_call(monkeypatch: Any) -> None:
    async def _fake_call(messages: Any, **kwargs: Any) -> AIMessage:
        assert kwargs.get("response_format") is None
        assert len(messages) >= 1
        return AIMessage(content="draft reply")

    monkeypatch.setattr(llm_service, "call", _fake_call)
    out = _run(generator(GraphState(messages=[HumanMessage(content="hi")])))
    assert out["messages"][-1].content == "draft reply"
    assert out["attempts"] == 1


def test_grader_checks_via_validated_output(monkeypatch: Any) -> None:
    seen: dict[str, Any] = {}

    async def _fake_call(messages: Any, **kwargs: Any) -> GradeDecision:
        seen["format"] = kwargs.get("response_format")
        return GradeDecision(approved=True, feedback="")

    monkeypatch.setattr(llm_service, "call", _fake_call)
    out = _run(
        grader(GraphState(messages=[HumanMessage(content="hi"), AIMessage(content="draft")]))
    )
    assert seen["format"] is GradeDecision
    assert out["approved"] is True


def test_reject_routes_back_with_feedback() -> None:
    rejected = GraphState(
        messages=[HumanMessage(content="hi"), AIMessage(content="draft-1")],
        attempts=1,
        approved=False,
        feedback="Be kinder.",
    )
    assert route_after_grader(rejected) == "generator"
    approved = GraphState(
        messages=[HumanMessage(content="hi"), AIMessage(content="draft-1")],
        attempts=1,
        approved=True,
        feedback="",
    )
    assert route_after_grader(approved) == "__end__"


def test_guard_accepts_last_below_three() -> None:
    guard = GraphState(
        messages=[HumanMessage(content="hi"), AIMessage(content="draft-3")],
        attempts=3,
        approved=False,
        feedback="Still rude.",
    )
    assert route_after_grader(guard) == "__end__"


def test_approve_first_try(monkeypatch: Any) -> None:
    async def _fake_call(messages: Any, **kwargs: Any) -> Any:
        if kwargs.get("response_format") is not None:
            assert kwargs["response_format"] is GradeDecision
            return GradeDecision(approved=True, feedback="")
        return AIMessage(content="polite reply")

    monkeypatch.setattr(llm_service, "call", _fake_call)
    agent = make_evaluator_agent()
    result = _run(agent.ainvoke({"messages": [HumanMessage(content="hello")]}))
    assert result["approved"] is True
    assert result["attempts"] == 1
    assert result["messages"][-1].content == "polite reply"


def test_reject_then_approve(monkeypatch: Any) -> None:
    drafts = ["draft-1", "draft-2"]
    grades = [
        GradeDecision(approved=False, feedback="Be kinder."),
        GradeDecision(approved=True),
    ]

    async def _fake_call(messages: Any, **kwargs: Any) -> Any:
        if kwargs.get("response_format") is not None:
            return grades.pop(0)
        return AIMessage(content=drafts.pop(0))

    monkeypatch.setattr(llm_service, "call", _fake_call)
    agent = make_evaluator_agent()
    result = _run(agent.ainvoke({"messages": [HumanMessage(content="hello")]}))
    assert result["approved"] is True
    assert result["attempts"] == 2
    assert result["messages"][-1].content == "draft-2"
    assert "Be kinder." in str([m.content for m in result["messages"]])


def test_guard_accepts_last_draft(monkeypatch: Any) -> None:
    count = {"n": 0}

    async def _fake_call(messages: Any, **kwargs: Any) -> Any:
        if kwargs.get("response_format") is not None:
            return GradeDecision(approved=False, feedback="Try again.")
        count["n"] += 1
        return AIMessage(content=f"draft-{count['n']}")

    monkeypatch.setattr(llm_service, "call", _fake_call)
    agent = make_evaluator_agent()
    result = _run(agent.ainvoke({"messages": [HumanMessage(content="hello")]}))
    assert count["n"] == 3
    assert result["attempts"] == 3
    assert result["messages"][-1].content == "draft-3"


def test_rude_to_approved_demo(monkeypatch: Any) -> None:
    drafts = ["You are wrong, deal with it.", "Happy to help with that calmly."]

    async def _fake_call(messages: Any, **kwargs: Any) -> Any:
        if kwargs.get("response_format") is not None:
            last_ai = next(
                m.content for m in reversed(messages) if isinstance(m, AIMessage) and m.content
            )
            if "deal with it" in str(last_ai):
                return GradeDecision(approved=False, feedback="Be polite and helpful.")
            return GradeDecision(approved=True, feedback="")
        return AIMessage(content=drafts.pop(0))

    monkeypatch.setattr(llm_service, "call", _fake_call)
    agent = make_evaluator_agent()
    result = _run(agent.ainvoke({"messages": [HumanMessage(content="help me")]}))
    assert result["approved"] is True
    assert result["attempts"] == 2
    assert result["messages"][-1].content == "Happy to help with that calmly."
