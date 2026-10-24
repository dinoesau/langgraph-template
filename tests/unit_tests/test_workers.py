"""Unit tests for S5c parallel workers.

No network and no containers: labels come from a routing fake llm
keyed on the prompt, so parallel call order cannot flake the suite.
"""

import asyncio
from typing import Any, cast

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from agent.state import GraphState
from agent.workers import (
    build_category_sync,
    build_sentiment_sync,
    build_ticket_graph,
    build_urgency_sync,
)
from tests.unit_tests.fakes import FakeLlm, fixed_state


class RoutingLlm(FakeLlm):
    """Fake llm that answers by worker kind found in the prompt."""

    def call(
        self,
        messages: list[BaseMessage],
        tools: list[Any] | None = None,
        response_format: Any | None = None,
    ) -> AIMessage:
        """Sync entry; routes the reply on the prompt text."""
        self.calls.append(list(messages))
        return self._route(str(messages[-1].content))

    async def acall(
        self,
        messages: list[BaseMessage],
        tools: list[Any] | None = None,
        response_format: Any | None = None,
    ) -> AIMessage:
        """Async entry with the same routing semantics."""
        self.calls.append(list(messages))
        return self._route(str(messages[-1].content))

    def _route(self, prompt: str) -> AIMessage:
        lowered = prompt.lower()
        if "sentiment" in lowered:
            return AIMessage(content="positive")
        if "urgency" in lowered:
            return AIMessage(content="high")
        return AIMessage(content="billing")


def _ticket(text: str, ticket_id: str = "T-1") -> GraphState:
    return cast(GraphState, {**fixed_state(text), "ticket_id": ticket_id})


def test_one_ticket_merges_into_single_card() -> None:
    llm = RoutingLlm()
    demo = build_ticket_graph(llm)
    result = demo.invoke(_ticket("My booking failed and I want a refund"))
    card = result["messages"][-1].content
    assert "Ticket T-1" in card
    assert "sentiment=positive" in card
    assert "urgency=high" in card
    assert "category=billing" in card
    assert "<ticket>" in str(llm.calls[0][0].content)


def test_worker_failure_degrades_its_field_only() -> None:
    sentiment = build_sentiment_sync(FakeLlm(failures=1))
    assert sentiment(fixed_state("late refund again")) == {"sentiment": "unknown"}

    urgency = build_urgency_sync(FakeLlm(["high"]))
    assert urgency(fixed_state("late refund again")) == {"urgency": "high"}

    category = build_category_sync(FakeLlm(["billing"]))
    assert category(fixed_state("late refund again")) == {"category": "billing"}


def test_no_llm_degrades_all_fields_but_card_still_merges() -> None:
    demo = build_ticket_graph(None)
    result = demo.invoke(_ticket("My booking failed", ticket_id="T-2"))
    card = result["messages"][-1].content
    assert "Ticket T-2" in card
    assert "sentiment=unknown" in card
    assert "urgency=unknown" in card
    assert "category=unknown" in card


def test_blank_ticket_rejected() -> None:
    demo = build_ticket_graph(RoutingLlm())
    result = demo.invoke(cast(GraphState, {"messages": [HumanMessage(content="   ")]}))
    assert len(result["messages"]) == 1
    assert "Ticket" not in str(result["messages"])


def test_async_ticket_graph() -> None:
    async def run() -> Any:
        demo = build_ticket_graph(RoutingLlm())
        return await demo.ainvoke(_ticket("async refund ticket", ticket_id="T-3"))

    result = asyncio.run(run())
    card = result["messages"][-1].content
    assert "Ticket T-3" in card
    assert "sentiment=positive" in card
