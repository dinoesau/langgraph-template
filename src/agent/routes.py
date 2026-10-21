"""Pure routing: state in, next node name out, no side effects."""

from typing import Literal

from langchain_core.messages import AIMessage

from agent.state import GraphState


def should_continue(state: GraphState) -> Literal["tools", "__end__"]:
    """Return the tools branch when the last AI message holds tool calls."""
    messages = state.get("messages", [])
    if not messages:
        return "__end__"
    last = messages[-1]
    if isinstance(last, AIMessage) and getattr(last, "tool_calls", None) and last.tool_calls:
        return "tools"
    return "__end__"
