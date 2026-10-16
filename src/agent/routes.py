"""Routing: pure chat/grader routes plus the S5b entry router with model assist."""

import logging
import re
from typing import Any, Literal

from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel, Field

from src.agent.nodes import MAX_GRADER_ITERATIONS
from src.agent.state import GraphState
from src.services.llm.service import llm_service

logger = logging.getLogger(__name__)

NextNode = Literal["tool_call", "__end__"]
NextEvaluatorNode = Literal["generator", "__end__"]
NextRouterNode = Literal["rag", "booking", "chat"]


class IntentDecision(BaseModel):
    """Validated router verdict; the LLM must return this shape."""

    intent: NextRouterNode = Field(description="One of rag, booking, or chat.")


_RAG_KEYWORDS = ("refund", "status", "policy")
_BOOKING_KEYWORDS = (
    "book",
    "booking",
    "appointment",
    "availability",
    "slot",
    "schedule",
    "reschedule",
    "tomorrow",
    "today",
    "date",
    "time",
)
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}|\d{1,2}:\d{2}|\b\d{1,2}\s?(am|pm)\b")


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


def _last_user_text(state: GraphState) -> str:
    """Return the last user message text, empty when there is none."""
    for message in reversed(state.get("messages", [])):
        if isinstance(message, HumanMessage):
            content = message.content
            return content if isinstance(content, str) else str(content)
    return ""


def _rule_route(text: str) -> NextRouterNode | None:
    """Deterministic rule: refund/status to RAG, date/time to booking."""
    lowered = text.lower()
    if any(keyword in lowered for keyword in _RAG_KEYWORDS):
        return "rag"
    if any(keyword in lowered for keyword in _BOOKING_KEYWORDS):
        return "booking"
    if _DATE_RE.search(text) is not None:
        return "booking"
    return None


def _coerce_intent(decision: Any) -> NextRouterNode:
    """Accept validated, dict, or duck-typed verdicts, defaulting to chat."""
    intent: Any = None
    if isinstance(decision, IntentDecision):
        intent = decision.intent
    elif isinstance(decision, dict):
        intent = decision.get("intent")
    else:
        intent = getattr(decision, "intent", None)
    if intent == "rag":
        return "rag"
    if intent == "booking":
        return "booking"
    return "chat"


async def intention_route(state: GraphState) -> NextRouterNode:
    """Send each request to RAG, booking, or chat, rule first then model."""
    text = _last_user_text(state)
    ruled = _rule_route(text)
    if ruled is not None:
        return ruled
    if not text.strip():
        return "chat"
    try:
        decision = await llm_service.call(
            [HumanMessage(content=f"Route this request to rag, booking, or chat: {text}")],
            response_format=IntentDecision,
        )
    except Exception:
        logger.exception("Router model assist failed, defaulting to chat")
        return "chat"
    return _coerce_intent(decision)
