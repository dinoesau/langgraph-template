"""Pure routing: state in, next node name out, no I/O."""

from typing import Literal

from langchain_core.messages import AIMessage

from src.agent.state import GraphState

NextNode = Literal["tool_call", "__end__"]


def route_after_chat(state: GraphState) -> NextNode:
    """Send replies with tool calls to the tool node, everything else out."""
    messages = state.get("messages", [])
    last = messages[-1] if messages else None
    if isinstance(last, AIMessage) and last.tool_calls:
        return "tool_call"
    return "__end__"
