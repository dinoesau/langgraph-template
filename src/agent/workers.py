"""Ticket summarization: parallel workers merged by one aggregator.

Three workers read the same ticket text and write disjoint keys, so
concurrent writes can never clobber each other and no reducer is needed.
The aggregator is the only writer of the merged card. A worker failure
degrades its field instead of dropping the card.
"""

import logging
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig, RunnableLambda
from langgraph.graph import END, START, StateGraph

from agent.nodes import last_human_text
from agent.state import GraphState

logger = logging.getLogger("agent")

DEGRADED = "unknown"


def _text(result: Any) -> str:
    """Extract reply text; non-string blocks stringify at the edge."""
    content = result.content if isinstance(result, AIMessage) else result
    if isinstance(content, str):
        return content
    return str(content)


def _clean(raw: str) -> str:
    """First non-blank line, or empty when the reply carries no text."""
    stripped = raw.strip()
    if not stripped:
        return ""
    return stripped.splitlines()[0].strip()


def _classify(llm: Any, kind: str, choices: str, text: str) -> str:
    """Run one classifier; degrade the field on any failure."""
    if llm is None:
        return DEGRADED
    prompt = (
        f"Classify the {kind} of this support ticket as one of {choices}. "
        f"Reply with only the label.\n<ticket>\n{text}\n</ticket>"
    )
    try:
        result = llm.call([HumanMessage(content=prompt)])
    except Exception as exc:  # noqa: BLE001 - worker failure degrades the field
        logger.warning("worker %s degraded: %s", kind, exc)
        return DEGRADED
    return _clean(_text(result)) or DEGRADED


async def _classify_async(llm: Any, kind: str, choices: str, text: str) -> str:
    """Run one classifier; degrade the field on any failure."""
    if llm is None:
        return DEGRADED
    prompt = (
        f"Classify the {kind} of this support ticket as one of {choices}. "
        f"Reply with only the label.\n<ticket>\n{text}\n</ticket>"
    )
    try:
        result = await llm.acall([HumanMessage(content=prompt)])
    except Exception as exc:  # noqa: BLE001 - worker failure degrades the field
        logger.warning("worker %s degraded: %s", kind, exc)
        return DEGRADED
    return _clean(_text(result)) or DEGRADED


def _ticket_text(state: GraphState) -> str | None:
    """Return the ticket text, or None when the input is blank."""
    try:
        return last_human_text(state.get("messages", []))
    except ValueError:
        return None


def build_sentiment_sync(llm: Any) -> Any:
    """Return the sync sentiment worker; writes the sentiment key only."""

    def sentiment_sync(state: GraphState, config: RunnableConfig | None = None) -> dict[str, Any]:
        text = _ticket_text(state)
        if text is None:
            return {"messages": []}
        return {"sentiment": _classify(llm, "sentiment", "positive, neutral, negative", text)}

    return sentiment_sync


def build_urgency_sync(llm: Any) -> Any:
    """Return the sync urgency worker; writes the urgency key only."""

    def urgency_sync(state: GraphState, config: RunnableConfig | None = None) -> dict[str, Any]:
        text = _ticket_text(state)
        if text is None:
            return {"messages": []}
        return {"urgency": _classify(llm, "urgency", "low, medium, high", text)}

    return urgency_sync


def build_category_sync(llm: Any) -> Any:
    """Return the sync category worker; writes the category key only."""

    def category_sync(state: GraphState, config: RunnableConfig | None = None) -> dict[str, Any]:
        text = _ticket_text(state)
        if text is None:
            return {"messages": []}
        return {"category": _classify(llm, "category", "billing, booking, technical, other", text)}

    return category_sync


def build_aggregator_sync() -> Any:
    """Return the sync aggregator; merges all fields plus the ticket id."""

    def aggregator_sync(state: GraphState, config: RunnableConfig | None = None) -> dict[str, Any]:
        text = _ticket_text(state)
        if text is None:
            return {"messages": []}
        ticket_id = state.get("ticket_id") or "n/a"
        card = (
            f"Ticket {ticket_id}: sentiment={state.get('sentiment') or DEGRADED}, "
            f"urgency={state.get('urgency') or DEGRADED}, "
            f"category={state.get('category') or DEGRADED}"
        )
        return {"messages": [AIMessage(content=card)]}

    return aggregator_sync


def build_sentiment_async(llm: Any) -> Any:
    """Return the async sentiment worker; writes the sentiment key only."""

    async def sentiment_async(
        state: GraphState, config: RunnableConfig | None = None
    ) -> dict[str, Any]:
        text = _ticket_text(state)
        if text is None:
            return {"messages": []}
        value = await _classify_async(llm, "sentiment", "positive, neutral, negative", text)
        return {"sentiment": value}

    return sentiment_async


def build_urgency_async(llm: Any) -> Any:
    """Return the async urgency worker; writes the urgency key only."""

    async def urgency_async(
        state: GraphState, config: RunnableConfig | None = None
    ) -> dict[str, Any]:
        text = _ticket_text(state)
        if text is None:
            return {"messages": []}
        value = await _classify_async(llm, "urgency", "low, medium, high", text)
        return {"urgency": value}

    return urgency_async


def build_category_async(llm: Any) -> Any:
    """Return the async category worker; writes the category key only."""

    async def category_async(
        state: GraphState, config: RunnableConfig | None = None
    ) -> dict[str, Any]:
        text = _ticket_text(state)
        if text is None:
            return {"messages": []}
        value = await _classify_async(llm, "category", "billing, booking, technical, other", text)
        return {"category": value}

    return category_async


def build_aggregator_async() -> Any:
    """Return the async aggregator; merges all fields plus the ticket id."""

    async def aggregator_async(
        state: GraphState, config: RunnableConfig | None = None
    ) -> dict[str, Any]:
        text = _ticket_text(state)
        if text is None:
            return {"messages": []}
        ticket_id = state.get("ticket_id") or "n/a"
        card = (
            f"Ticket {ticket_id}: sentiment={state.get('sentiment') or DEGRADED}, "
            f"urgency={state.get('urgency') or DEGRADED}, "
            f"category={state.get('category') or DEGRADED}"
        )
        return {"messages": [AIMessage(content=card)]}

    return aggregator_async


def build_ticket_graph(llm: Any) -> Any:
    """Compile the standalone ticket demo: fan-out, then one aggregator."""
    sentiment_sync = build_sentiment_sync(llm)
    urgency_sync = build_urgency_sync(llm)
    category_sync = build_category_sync(llm)
    aggregator_sync = build_aggregator_sync()
    sentiment_async = build_sentiment_async(llm)
    urgency_async = build_urgency_async(llm)
    category_async = build_category_async(llm)
    aggregator_async = build_aggregator_async()

    builder = StateGraph(GraphState)
    builder.add_node("sentiment", RunnableLambda(sentiment_sync, afunc=sentiment_async))
    builder.add_node("urgency", RunnableLambda(urgency_sync, afunc=urgency_async))
    builder.add_node("category", RunnableLambda(category_sync, afunc=category_async))
    builder.add_node("aggregator", RunnableLambda(aggregator_sync, afunc=aggregator_async))
    builder.add_edge(START, "sentiment")
    builder.add_edge(START, "urgency")
    builder.add_edge(START, "category")
    builder.add_edge("sentiment", "aggregator")
    builder.add_edge("urgency", "aggregator")
    builder.add_edge("category", "aggregator")
    builder.add_edge("aggregator", END)
    return builder.compile()
