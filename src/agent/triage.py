"""S5c parallel workers: ticket fan-out merged into one triage card."""

import operator
from typing import Annotated, Any, TypedDict

from langchain_core.messages import BaseMessage, HumanMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send

from src.services.llm.service import llm_service


class TriageCard(TypedDict):
    """Single triage card with ticket id plus three worker fields."""

    ticket_id: str
    sentiment: str
    urgency: str
    category: str


class TriageState(TypedDict):
    """Ticket text in, shared results list, one merged card out."""

    ticket_id: str
    ticket_text: str
    results: Annotated[list[dict[str, str]], operator.add]
    card: TriageCard


def _text_of(response: Any) -> str:
    """Extract plain text from an LLM reply without guessing structure."""
    if isinstance(response, BaseMessage):
        content = response.content
        if isinstance(content, str):
            return content
        return str(content)
    if isinstance(response, str):
        return response
    return str(response)


def _clean(value: str) -> str:
    cleaned = value.strip()
    return cleaned if cleaned else "unknown"


async def sentiment_worker(state: TriageState) -> dict[str, list[dict[str, str]]]:
    """Classify ticket sentiment reading the shared ticket text."""
    text = state.get("ticket_text", "")
    reply = await llm_service.call(
        [HumanMessage(content=f"Classify sentiment of this ticket: {text}")]
    )
    return {"results": [{"sentiment": _clean(_text_of(reply))}]}


async def urgency_worker(state: TriageState) -> dict[str, list[dict[str, str]]]:
    """Classify ticket urgency reading the shared ticket text."""
    text = state.get("ticket_text", "")
    reply = await llm_service.call(
        [HumanMessage(content=f"Classify urgency of this ticket: {text}")]
    )
    return {"results": [{"urgency": _clean(_text_of(reply))}]}


async def category_worker(state: TriageState) -> dict[str, list[dict[str, str]]]:
    """Classify ticket category reading the shared ticket text."""
    text = state.get("ticket_text", "")
    reply = await llm_service.call(
        [HumanMessage(content=f"Classify category of this ticket: {text}")]
    )
    return {"results": [{"category": _clean(_text_of(reply))}]}


def fan_out(state: TriageState) -> list[Send]:
    """Fan out to three workers, each reading the same ticket text."""
    payload = {"ticket_id": state.get("ticket_id", ""), "ticket_text": state.get("ticket_text", "")}
    return [
        Send("sentiment", dict(payload)),
        Send("urgency", dict(payload)),
        Send("category", dict(payload)),
    ]


async def aggregator(state: TriageState) -> dict[str, TriageCard]:
    """Merge shared results plus ticket id into one triage card."""
    merged: dict[str, str] = {}
    for item in state.get("results", []):
        merged.update(item)
    card: TriageCard = {
        "ticket_id": state.get("ticket_id", ""),
        "sentiment": merged.get("sentiment", "unknown"),
        "urgency": merged.get("urgency", "unknown"),
        "category": merged.get("category", "unknown"),
    }
    return {"card": card}


def make_triage_graph(
    checkpointer: BaseCheckpointSaver[str] | None = None,
) -> CompiledStateGraph[TriageState, Any, Any, Any]:
    """Compile ticket fan-out, persisting per thread only with a checkpointer."""
    builder = StateGraph(TriageState)
    builder.add_node("sentiment", sentiment_worker)
    builder.add_node("urgency", urgency_worker)
    builder.add_node("category", category_worker)
    builder.add_node("aggregator", aggregator)
    builder.add_conditional_edges(START, fan_out)
    builder.add_edge("sentiment", "aggregator")
    builder.add_edge("urgency", "aggregator")
    builder.add_edge("category", "aggregator")
    builder.add_edge("aggregator", END)
    return builder.compile(checkpointer=checkpointer)


triage_graph = make_triage_graph()
