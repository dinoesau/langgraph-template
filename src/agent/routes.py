"""Pure routing: state in, next node name out, no I/O."""

from typing import Literal

from langchain_core.messages import AIMessage

from src.agent.nodes import MAX_GRADER_ITERATIONS
from src.agent.state import GraphState

NextNode = Literal["tool_call", "__end__"]
NextEvaluatorNode = Literal["generator", "__end__"]


def route_after_chat(state: GraphState) -> NextNode:
    """Send replies with tool calls to the tool node, everything else out."""
    messages = state.get("messages", [])
    last = messages[-1] if messages else None
    if isinstance(last, AIMessage) and last.tool_calls:
        return "tool_call"
    return "__end__"


def route_after_grader(state: GraphState) -> NextEvaluatorNode:
    """Send rejected drafts back; approved or guard-capped drafts exit."""
    if bool(state.get("approved", False)):
        return "__end__"
    if int(state.get("attempts", 0)) >= MAX_GRADER_ITERATIONS:
        return "__end__"
    return "generator"
