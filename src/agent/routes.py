"""Pure routing: state in, next node name out, no side effects.

Thin wrapper over the prebuilt ``tools_condition`` so the seam stays
documented here while the branching logic stays canonical.
"""

import re
from typing import Any, Literal, cast

from langgraph.prebuilt import tools_condition

from agent.nodes import last_human_text
from agent.state import GraphState

TriageTarget = Literal["retrieval", "booking", "chat"]

TRIAGE_TARGETS: dict[str, str] = {"retrieval": "retrieve", "booking": "chat", "chat": "chat"}

RETRIEVAL_WORDS = (
    "refund",
    "refunds",
    "return",
    "returns",
    "policy",
    "policies",
    "status",
    "warranty",
    "warranties",
    "guarantee",
    "guarantees",
)
BOOKING_WORDS = (
    "date",
    "dates",
    "time",
    "times",
    "slot",
    "slots",
    "book",
    "books",
    "booked",
    "booking",
    "appointment",
    "appointments",
    "tomorrow",
    "schedule",
    "schedules",
    "scheduled",
    "available",
    "availability",
    "reserve",
    "reservation",
    "reservations",
)

_RETRIEVAL_RE = re.compile(r"\b(?:" + "|".join(map(re.escape, RETRIEVAL_WORDS)) + r")\b")
_BOOKING_RE = re.compile(r"\b(?:" + "|".join(map(re.escape, BOOKING_WORDS)) + r")\b")


def match_family(text: str, words: tuple[str, ...]) -> bool:
    """Whole-word match over explicit variants; extend tuples, never stems."""
    if words == RETRIEVAL_WORDS:
        return bool(_RETRIEVAL_RE.search(text))
    if words == BOOKING_WORDS:
        return bool(_BOOKING_RE.search(text))
    return any(re.search(rf"\b{re.escape(word)}\b", text) for word in words)


def should_continue(state: GraphState) -> Literal["tools", "__end__"]:
    """Return the tools branch when the last AI message holds tool calls."""
    return tools_condition(cast(dict[str, Any], state))


def triage_route(state: GraphState) -> Literal["retrieval", "booking", "chat", "triage_assist"]:
    """Route by rule first; both families matched means genuine ambiguity."""
    text = last_human_text(state.get("messages", [])).lower()
    refund = match_family(text, RETRIEVAL_WORDS)
    booking = match_family(text, BOOKING_WORDS)
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
