"""Unit tests for S5b triage router.

No network and no containers: routing is rule-first pure, the assist
uses a scripted fake, and end-to-end runs use the default echofallback
or scripted tool-call replies.
"""

import asyncio
from typing import Any, cast

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agent.graph import graph, make_the_agent
from agent.retrieval import UNKNOWN
from agent.routes import triage_decision_route, triage_route
from agent.triage import TriageLabel, build_triage_assist_sync
from tests.unit_tests.fakes import FakeLlm, fixed_state


def test_rule_families_route_to_the_right_branch() -> None:
    assert triage_route(fixed_state("what is your refund policy")) == "retrieval"
    assert triage_route(fixed_state("what is my ticket status")) == "retrieval"
    assert triage_route(fixed_state("our return policies")) == "retrieval"
    assert triage_route(fixed_state("book me tomorrow 10am")) == "booking"
    assert triage_route(fixed_state("is there a slot at 3pm")) == "booking"
    assert triage_route(fixed_state("check my reservations")) == "booking"
    assert triage_route(fixed_state("hello there")) == "chat"


def test_substring_lookalikes_do_not_route() -> None:
    assert triage_route(fixed_state("sometimes I wonder")) == "chat"
    assert triage_route(fixed_state("here is my update")) == "chat"
    assert triage_route(fixed_state("rebook my flight")) == "chat"


def test_both_families_go_to_assist() -> None:
    assert triage_route(fixed_state("refund status for my booking tomorrow")) == "triage_assist"


def test_assist_stores_structured_label() -> None:
    assist = build_triage_assist_sync(FakeLlm(["booking"]))
    result = assist(fixed_state("refund status for my booking tomorrow"))
    assert result == {"triage_decision": "booking"}


def test_assist_accepts_structured_verdict() -> None:
    assist = build_triage_assist_sync(LabelLlm())
    result = assist(fixed_state("refund status for my booking tomorrow"))
    assert result == {"triage_decision": "retrieval"}


def test_assist_falls_back_without_model() -> None:
    assist = build_triage_assist_sync(None)
    assert assist(fixed_state("refund and booking clash")) == {"triage_decision": "retrieval"}
    assert assist(fixed_state("just saying hi")) == {"triage_decision": "chat"}


def test_assist_degrades_to_fallback_on_model_failure() -> None:
    assist = build_triage_assist_sync(FakeLlm(failures=2))
    assert assist(fixed_state("refund and booking clash")) == {"triage_decision": "retrieval"}
    assert assist(fixed_state("book and date clash")) == {"triage_decision": "booking"}


def test_assist_unparseable_falls_to_chat() -> None:
    assist = build_triage_assist_sync(FakeLlm(["maybe"]))
    assert assist(fixed_state("refund and booking clash")) == {"triage_decision": "chat"}


def test_decision_route_reads_stored_label() -> None:
    assert triage_decision_route(cast(Any, {"triage_decision": "booking"})) == "booking"
    assert triage_decision_route(cast(Any, {"triage_decision": "retrieval"})) == "retrieval"
    assert triage_decision_route(cast(Any, {})) == "chat"


def test_end_to_end_retrieval_branch_answers_unknown() -> None:
    result = graph.invoke({"messages": [HumanMessage(content="what is your refund policy")]})
    assert result["messages"][-1].content == UNKNOWN


def test_end_to_end_chat_branch_echoes() -> None:
    result = graph.invoke({"messages": [HumanMessage(content="hello there")]})
    assert result["messages"][-1].content == "Echo: hello there"


def test_chat_branch_clears_stale_passages() -> None:
    state = cast(Any, {"messages": [HumanMessage(content="hello there")], "passages": ["stale"]})
    result = graph.invoke(state)
    assert result["messages"][-1].content == "Echo: hello there"
    assert result.get("passages") == []


def test_end_to_end_booking_branch_runs_tool_loop() -> None:
    first = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "get_availability",
                "args": {"date": "tomorrow"},
                "id": "call_1",
                "type": "tool_call",
            }
        ],
    )
    llm = FakeLlm([first, "Done, see you tomorrow at 10am."])
    agent = make_the_agent(llm=llm)
    result = agent.invoke({"messages": [HumanMessage(content="book me tomorrow 10am")]})
    assert result["messages"][-1].content == "Done, see you tomorrow at 10am."


def test_bad_input_fails_fast_at_triage() -> None:
    try:
        graph.invoke({"messages": [HumanMessage(content="   ")]})
    except ValueError:
        return
    raise AssertionError("blank input must raise ValueError")


def test_end_to_end_ambiguous_input_goes_through_assist() -> None:
    llm = FakeLlm(["booking", "Sure, I can help with that booking."])
    agent = make_the_agent(llm=llm)
    result = agent.invoke(
        {"messages": [HumanMessage(content="refund status for my booking tomorrow")]}
    )
    assert result.get("triage_decision") == "booking"
    assert result["messages"][-1].content == "Sure, I can help with that booking."
    assert "booking" in str(llm.calls[0][0].content).lower()


def test_end_to_end_ambiguous_input_async() -> None:
    async def run() -> Any:
        llm = FakeLlm(["retrieval", "Cited answer [1]."])
        agent = make_the_agent(llm=llm)
        return await agent.ainvoke(
            {"messages": [HumanMessage(content="refund and booking clash tomorrow")]}
        )

    result = asyncio.run(run())
    assert result.get("triage_decision") == "retrieval"


def test_stubborn_tool_loop_hits_recursion_cap() -> None:
    from langgraph.errors import GraphRecursionError

    stubborn = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "get_availability",
                "args": {"date": "tomorrow"},
                "id": "s",
                "type": "tool_call",
            }
        ],
    )
    llm = FakeLlm([stubborn, stubborn, stubborn, stubborn])
    agent = make_the_agent(llm=llm)
    with pytest.raises(GraphRecursionError):
        agent.invoke(
            {"messages": [HumanMessage(content="book me tomorrow")]},
            config={"recursion_limit": 3},
        )


class LabelLlm(FakeLlm):
    """Fake llm that answers assist prompts with structured labels."""

    def call(
        self,
        messages: list[Any],
        tools: list[Any] | None = None,
        response_format: Any | None = None,
    ) -> Any:
        """Sync entry; structured label when a format is requested."""
        self.calls.append(messages)
        if response_format is not None:
            return TriageLabel(decision="retrieval")
        return AIMessage(content="chat")
