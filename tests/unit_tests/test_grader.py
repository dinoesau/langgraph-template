"""Unit tests for S5d generator plus grader.

No network and no containers: drafts and verdicts come from scripted
fakes. Call order is sequential, so scripted reply queues stay deterministic.
"""

import asyncio
from typing import Any, cast

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from agent.grader import (
    GradeVerdict,
    build_drafter_sync,
    build_grader_graph,
    build_grader_sync,
)
from agent.routes import grade_route
from agent.state import GraphState
from tests.unit_tests.fakes import FakeLlm, fixed_state


def test_rude_draft_converges_to_approved_reply() -> None:
    llm = FakeLlm(
        [
            "You are wrong, whatever.",
            "REJECT: rude tone, apologize and offer a refund",
            "We are sorry for the trouble. Your refund is on the way.",
            "APPROVED",
        ]
    )
    demo = build_grader_graph(llm)
    result = demo.invoke(fixed_state("My refund is late and I am angry"))
    assert (
        result["messages"][-1].content == "We are sorry for the trouble. Your refund is on the way."
    )
    assert (result.get("attempts") or 0) == 0
    assert not result.get("grade_feedback")
    assert "rude tone" in str(llm.calls[2])


def test_guard_accepts_last_draft() -> None:
    llm = FakeLlm(["Still rude.", "REJECT: still rude"] * 3)
    demo = build_grader_graph(llm)
    result = demo.invoke(fixed_state("My refund is late"))
    assert result["messages"][-1].content == "Still rude."
    assert (result.get("attempts") or 0) == 0
    assert not result.get("grade_feedback")
    assert len(llm.calls) == 5


def test_guard_disengages_for_next_turn() -> None:
    grader = build_grader_sync(FakeLlm(["REJECT: still curt"]))
    guarded = cast(
        GraphState,
        {
            "messages": [HumanMessage(content="late"), AIMessage(content="Rude.")],
            "attempts": 2,
        },
    )
    assert grader(guarded) == {"attempts": 0, "grade_feedback": ""}
    fresh = cast(GraphState, {"messages": [AIMessage(content="Another try.")]})
    assert grader(fresh)["attempts"] == 1


def test_structured_verdict_path() -> None:
    llm = VerdictLlm()
    demo = build_grader_graph(llm)
    result = demo.invoke(fixed_state("My refund is late"))
    assert (result.get("attempts") or 0) == 0
    assert result["messages"][-1].content == "A curt draft."


def test_no_llm_approves_without_grading() -> None:
    demo = build_grader_graph(None)
    result = demo.invoke(fixed_state("My refund is late"))
    assert result["messages"][-1].content == "Echo: My refund is late"
    assert (result.get("attempts") or 0) == 0


def test_drafter_rejects_blank_input() -> None:
    drafter = build_drafter_sync(FakeLlm())
    try:
        drafter({"messages": [HumanMessage(content="   ")]})
    except ValueError:
        return
    raise AssertionError("blank input must raise ValueError")


def test_grade_route_is_pure() -> None:
    assert grade_route({"messages": [], "grade_feedback": "fix tone"}) == "drafter"
    assert grade_route({"messages": []}) == "end"


def test_async_grader_graph() -> None:
    async def run() -> Any:
        llm = FakeLlm(["Rude.", "REJECT: rude", "Polite fix.", "APPROVED"])
        demo = build_grader_graph(llm)
        return await demo.ainvoke(fixed_state("async refund complaint"))

    result = asyncio.run(run())
    assert result["messages"][-1].content == "Polite fix."


class VerdictLlm:
    """Fake llm that answers drafts with text and grades with structured verdicts."""

    def __init__(self) -> None:
        self.calls = 0

    def call(
        self,
        messages: list[BaseMessage],
        tools: list[Any] | None = None,
        response_format: Any | None = None,
    ) -> AIMessage | GradeVerdict:
        """Sync entry; structured verdict when a format is requested."""
        self.calls += 1
        if response_format is not None:
            return GradeVerdict(approved=False, feedback="too curt")
        return AIMessage(content="A curt draft.")

    async def acall(
        self,
        messages: list[BaseMessage],
        tools: list[Any] | None = None,
        response_format: Any | None = None,
    ) -> AIMessage | GradeVerdict:
        """Async entry with the same semantics as the sync entry."""
        return self.call(messages, tools, response_format)
