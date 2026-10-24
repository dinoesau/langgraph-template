"""Triage: pure rule-first routing with model assist only on ambiguity.

Refund or status wording reaches retrieval, date or time wording reaches
booking, and everything else reaches free chat. When both families match,
an assist node resolves the ambiguity and a pure decision route reads
its stored label. Unclassifiable input falls through to free chat.
"""

import logging
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel

from agent.nodes import last_human_text
from agent.routes import BOOKING_WORDS, RETRIEVAL_WORDS, TriageTarget
from agent.state import GraphState

logger = logging.getLogger("agent")


class TriageLabel(BaseModel):
    """Validated assist output: exactly one triage destination."""

    decision: TriageTarget


def _normalize_label(result: Any) -> TriageTarget:
    """Coerce structured or plain-text labels; anything else falls to chat."""
    if isinstance(result, TriageLabel):
        return result.decision
    content = result.content if isinstance(result, AIMessage) else result
    text = content.strip().lower() if isinstance(content, str) else str(content).strip().lower()
    if text in ("retrieval", "booking", "chat"):
        return text  # type: ignore[return-value]
    logger.warning("triage assist unparseable, falling through to chat")
    return "chat"


def _assist_messages(text: str) -> list[BaseMessage]:
    """Assist prompt; the model picks exactly one destination."""
    return [
        SystemMessage(
            content="Triage this request. Reply with exactly one word: "
            "retrieval for refund or policy questions, booking for date or "
            "time requests, else chat."
        ),
        HumanMessage(content=text),
    ]


def _fallback_label(text: str) -> TriageTarget:
    """Deterministic no-model assist; refund wording wins ties."""
    lowered = text.lower()
    if any(word in lowered for word in RETRIEVAL_WORDS):
        return "retrieval"
    if any(word in lowered for word in BOOKING_WORDS):
        return "booking"
    return "chat"


def build_triage_assist_sync(llm: Any) -> Any:
    """Return the sync assist node; stores the label, never routes."""

    def triage_assist_sync(
        state: GraphState, config: RunnableConfig | None = None
    ) -> dict[str, Any]:
        text = last_human_text(state.get("messages", []))
        if llm is None:
            return {"triage_decision": _fallback_label(text)}
        result = llm.call(_assist_messages(text), response_format=TriageLabel)
        return {"triage_decision": _normalize_label(result)}

    return triage_assist_sync


def build_triage_assist_async(llm: Any) -> Any:
    """Return the async assist node; stores the label, never routes."""

    async def triage_assist_async(
        state: GraphState, config: RunnableConfig | None = None
    ) -> dict[str, Any]:
        text = last_human_text(state.get("messages", []))
        if llm is None:
            return {"triage_decision": _fallback_label(text)}
        result = await llm.acall(_assist_messages(text), response_format=TriageLabel)
        return {"triage_decision": _normalize_label(result)}

    return triage_assist_async
