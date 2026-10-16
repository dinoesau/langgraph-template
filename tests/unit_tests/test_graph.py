"""Slice 3 seam: chat -> tool_call -> chat loop with concurrent tools."""

import asyncio
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import MemorySaver

from src.agent.graph import graph, make_the_agent
from src.agent.nodes import tool_call
from src.agent.routes import route_after_chat
from src.agent.state import GraphState
from src.services.llm.service import llm_service


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _tool_call_msg(*calls: dict[str, Any]) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": call["name"],
                "args": call["args"],
                "id": call["id"],
                "type": "tool_call",
            }
            for call in calls
        ],
    )


def test_route_after_chat() -> None:
    with_tools = GraphState(messages=[_tool_call_msg({"name": "t", "args": {}, "id": "c1"})])
    assert route_after_chat(with_tools) == "tool_call"
    assert route_after_chat({"messages": [_ok("hi")]}) == "__end__"
    assert route_after_chat({"messages": []}) == "__end__"


def _ok(text: str) -> AIMessage:
    return AIMessage(content=text)


def test_time_request_loops_until_confirmation(monkeypatch: Any) -> None:
    async def _fake_call(messages: Any, **kwargs: Any) -> AIMessage:
        step = len([m for m in messages if isinstance(m, AIMessage)])
        if step == 0:
            return _tool_call_msg(
                {
                    "name": "get_availability",
                    "args": {"date": "2026-10-23"},
                    "id": "c1",
                }
            )
        if step == 1:
            return _tool_call_msg(
                {
                    "name": "book_appointment",
                    "args": {"date": "2026-10-23", "time": "10:00"},
                    "id": "c2",
                }
            )
        return _ok("Booked 2026-10-23 at 10:00.")

    monkeypatch.setattr(llm_service, "call", _fake_call)
    result = _run(graph.ainvoke({"messages": [HumanMessage(content="book me 10am")]}))
    texts = [m.content for m in result["messages"] if isinstance(m, AIMessage)]
    assert texts[-1] == "Booked 2026-10-23 at 10:00."
    tool_msgs = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_msgs) == 2
    assert "2026-10-23" in str(tool_msgs[0].content)


def test_no_tool_calls_ends_directly(monkeypatch: Any) -> None:
    async def _fake_call(messages: Any, **kwargs: Any) -> AIMessage:
        return _ok("hello back")

    monkeypatch.setattr(llm_service, "call", _fake_call)
    result = _run(graph.ainvoke({"messages": [HumanMessage(content="hello")]}))
    assert result["messages"][-1].content == "hello back"
    assert not [m for m in result["messages"] if isinstance(m, ToolMessage)]


def test_tool_node_runs_calls_concurrently(monkeypatch: Any) -> None:
    entered: list[str] = []

    async def _slow_ainvoke(self: Any, *args: Any, **kwargs: Any) -> ToolMessage:
        entered.append("in")
        await asyncio.sleep(0.05)
        entered.append("out")
        call = args[0] if args else kwargs.get("input", {})
        call_id = call.get("id", "") if isinstance(call, dict) else ""
        return ToolMessage(content="slow", tool_call_id=call_id, name="slow")

    monkeypatch.setattr(StructuredTool, "ainvoke", _slow_ainvoke)
    state = GraphState(
        messages=[
            _tool_call_msg(
                {"name": "get_availability", "args": {"date": "2026-10-24"}, "id": "c1"},
                {"name": "get_availability", "args": {"date": "2026-10-25"}, "id": "c2"},
            )
        ]
    )
    out = _run(tool_call(state))
    assert [m.tool_call_id for m in out["messages"]] == ["c1", "c2"]
    assert entered == ["in", "in", "out", "out"]


def test_unknown_tool_becomes_message_not_crash() -> None:
    state = GraphState(messages=[_tool_call_msg({"name": "nope", "args": {}, "id": "c9"})])
    out = _run(tool_call(state))
    assert len(out["messages"]) == 1
    assert "Unknown tool" in str(out["messages"][0].content)


def test_factory_still_injects_checkpointer(monkeypatch: Any) -> None:
    async def _fake_call(messages: Any, **kwargs: Any) -> AIMessage:
        return _ok("remembered")

    monkeypatch.setattr(llm_service, "call", _fake_call)
    agent = make_the_agent(checkpointer=MemorySaver())
    first = _run(
        agent.ainvoke(
            {"messages": [HumanMessage(content="hi")]},
            config={"configurable": {"thread_id": "loop-t1"}},
        )
    )
    second = _run(
        agent.ainvoke(
            {"messages": [HumanMessage(content="again")]},
            config={"configurable": {"thread_id": "loop-t1"}},
        )
    )
    assert len(second["messages"]) > len(first["messages"])
    assert second["messages"][-1].content == "remembered"
