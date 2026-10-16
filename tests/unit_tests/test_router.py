"""S5b seam: entry router sends refund/status to RAG, date/time to booking, rest to chat.

Seams under test (highest first):
- intention_route rule plus model assist (catches deterministic triggers plus
  ambiguous fallback via structured output; misses real model wording).
- router graph with booking subgraph (catches end to end wiring for all three
  families sharing GraphState; misses real provider latency).
- persistence inheritance (catches parent checkpointer covering the subgraph
  without extra setup; misses Postgres specifics, covered by integration).
"""

import asyncio
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver

from src.agent.state import GraphState
from src.services.llm.service import llm_service


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _state(text: str) -> GraphState:
    return GraphState(messages=[HumanMessage(content=text)])


def test_rule_sends_refund_to_rag() -> None:
    from src.agent.routes import intention_route

    out = _run(intention_route(_state("what is your refund policy")))
    assert out == "rag"


def test_rule_sends_status_to_rag() -> None:
    from src.agent.routes import intention_route

    out = _run(intention_route(_state("what is the status of ord-1")))
    assert out == "rag"


def test_rule_sends_date_time_to_booking() -> None:
    from src.agent.routes import intention_route

    assert _run(intention_route(_state("book me on 2026-10-23"))) == "booking"
    assert _run(intention_route(_state("what time slots on 2026-10-24"))) == "booking"
    assert _run(intention_route(_state("book me tomorrow at 10am"))) == "booking"


def test_model_assist_routes_ambiguous_via_structured_output(monkeypatch: Any) -> None:
    from src.agent.routes import IntentDecision, intention_route

    seen: dict[str, Any] = {}

    async def _fake_call(messages: Any, **kwargs: Any) -> Any:
        seen["format"] = kwargs.get("response_format")
        return IntentDecision(intent="chat")

    monkeypatch.setattr(llm_service, "call", _fake_call)
    out = _run(intention_route(_state("hello there")))
    assert out == "chat"
    assert seen["format"] is IntentDecision


def test_model_assist_can_pick_booking_for_ambiguous(monkeypatch: Any) -> None:
    from src.agent.routes import IntentDecision, intention_route

    async def _fake_call(messages: Any, **kwargs: Any) -> Any:
        assert kwargs.get("response_format") is IntentDecision
        return {"intent": "booking"}

    monkeypatch.setattr(llm_service, "call", _fake_call)
    out = _run(intention_route(_state("help me out")))
    assert out == "booking"


def _tool_call_msg(name: str, args: dict[str, str], call_id: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}],
    )


def test_router_sends_all_three_families_end_to_end(monkeypatch: Any) -> None:
    from src.agent.graph import make_router_agent

    async def _fake_call(messages: Any, **kwargs: Any) -> Any:
        if kwargs.get("response_format") is not None:
            return AIMessage(content="chat")
        texts = " ".join(str(m.content) for m in messages if hasattr(m, "content"))
        if "PASSAGES" in texts or "refund policy" in texts.lower():
            return AIMessage(content="Full refunds within 30 days [1].")
        steps = len([m for m in messages if isinstance(m, AIMessage)])
        if steps == 0:
            return _tool_call_msg("get_availability", {"date": "2026-10-23"}, "c1")
        if steps == 1:
            return _tool_call_msg("book_appointment", {"date": "2026-10-23", "time": "10:00"}, "c2")
        return AIMessage(content="Booked 2026-10-23 at 10:00.")

    monkeypatch.setattr(llm_service, "call", _fake_call)
    router = make_router_agent()

    rag = _run(router.ainvoke({"messages": [HumanMessage(content="refund please")]}))
    assert "30 days" in str(rag["messages"][-1].content)

    booking = _run(router.ainvoke({"messages": [HumanMessage(content="book 2026-10-23 at 10:00")]}))
    assert "Booked 2026-10-23" in str(booking["messages"][-1].content)

    async def _chat_only(messages: Any, **kwargs: Any) -> Any:
        if kwargs.get("response_format") is not None:
            from src.agent.routes import IntentDecision

            return IntentDecision(intent="chat")
        return AIMessage(content="hello back")

    monkeypatch.setattr(llm_service, "call", _chat_only)
    chat = _run(router.ainvoke({"messages": [HumanMessage(content="hello there")]}))
    assert str(chat["messages"][-1].content) == "hello back"


def test_booking_subgraph_shares_state_and_inherits_checkpointer(
    monkeypatch: Any,
) -> None:
    from src.agent.graph import make_router_agent

    async def _fake_call(messages: Any, **kwargs: Any) -> Any:
        if kwargs.get("response_format") is not None:
            from src.agent.routes import IntentDecision

            return IntentDecision(intent="booking")
        steps = len([m for m in messages if isinstance(m, AIMessage)])
        if steps == 0:
            return _tool_call_msg("get_availability", {"date": "2026-10-24"}, "c1")
        return AIMessage(content="done booking")

    monkeypatch.setattr(llm_service, "call", _fake_call)
    router = make_router_agent(checkpointer=MemorySaver())
    config: RunnableConfig = {"configurable": {"thread_id": "router-t1"}}
    first = _run(router.ainvoke({"messages": [HumanMessage(content="need a slot")]}, config=config))
    second = _run(router.ainvoke({"messages": [HumanMessage(content="again")]}, config=config))
    assert len(second["messages"]) > len(first["messages"])
    assert "done booking" in str(second["messages"][-1].content)
