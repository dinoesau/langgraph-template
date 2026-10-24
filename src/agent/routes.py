"""Pure routing: state in, next node name out, no side effects.

Thin wrapper over the prebuilt ``tools_condition`` so the seam stays
documented here while the branching logic stays canonical.
"""

from typing import Any, Literal, cast

from langgraph.prebuilt import tools_condition

from agent.nodes import last_human_text
from agent.state import GraphState

TriageTarget = Literal["retrieval", "booking", "chat"]

RETRIEVAL_WORDS = ("refund", "return", "policy", "status", "warranty", "guarantee")
BOOKING_WORDS = (
    "date",
    "time",
    "slot",
    "book",
    "appointment",
    "tomorrow",
    "schedule",
    "available",
    "availability",
    "reserve",
)


RETRIEVAL_WORDS = ("refund", "return", "policy", "status", "warranty", "guarantee")
BOOKING_WORDS = (
    "date",
    "time",
    "slot",
    "book",
    "appointment",
    "tomorrow",
    "schedule",
    "available",
    "availability",
    "reserve",
)


def should_continue(state: GraphState) -> Literal["tools", "__end__"]:
    """Return the tools branch when the last AI message holds tool calls."""
    return tools_condition(cast(dict[str, Any], state))


def triage_route(state: GraphState) -> Literal["retrieval", "booking", "chat", "triage_assist"]:
    """Route by rule first; both families matched means genuine ambiguity."""
    text = last_human_text(state.get("messages", [])).lower()
    refund = any(word in text for word in RETRIEVAL_WORDS)
    booking = any(word in text for word in BOOKING_WORDS)
    if refund and booking:
        return "triage_assist"
    if refund:
        return "retrieval"
    if booking:
        return "booking"
    return "chat"


def triage_decision_route(state: GraphState) -> TriageTarget:
    """Read the stored assist label; unclassifiable input falls to chat."""
    return state.get("triage_decision") or "chat"


def grade_route(state: GraphState) -> Literal["drafter", "end"]:
    """Return the grader-loop branch; feedback present means re-draft."""
    if state.get("grade_feedback"):
        return "drafter"
    return "end"
