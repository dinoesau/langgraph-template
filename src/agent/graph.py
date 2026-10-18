"""Echo loop: proves the deploy contract with no model and no storage."""

import logging
from collections.abc import Sequence
from typing import Any, Protocol

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig, RunnableLambda
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.config import get_store
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.base import BaseStore

from agent.state import GraphState
from core.memory import (
    DEFAULT_TOP_K,
    build_memory_key,
    format_recall,
    memory_namespace,
    normalize_query,
    recall_texts,
    valid_user_id,
)

logger = logging.getLogger("agent")


class RecallCachePort(Protocol):
    """Cache port owned by the loop; adapters satisfy it structurally."""

    def get_sync(self, key: str) -> str | None: ...
    def set_sync(self, key: str, value: str, ttl: int) -> None: ...
    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str, ttl: int) -> None: ...


def _last_human_text(messages: Sequence[BaseMessage]) -> str:
    """Return the last non-blank human text or raise ValueError."""
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            content = message.content
            if isinstance(content, str) and content.strip():
                return content
    raise ValueError("state must carry a non-empty human message")


def _request_user_id(config: RunnableConfig | None) -> str:
    """Read the transitional caller id from configurable without failing."""
    if config is None:
        return ""
    configurable = config.get("configurable") or {}
    raw = configurable.get("user_id", "")
    return valid_user_id(raw) if isinstance(raw, str) else ""


def _recall_sync(
    store: BaseStore, cache: RecallCachePort | None, ttl: int, user_id: str, query: str
) -> str:
    """Load per-user recall on the sync path; empty means inject nothing."""
    normalized = normalize_query(query)
    key = build_memory_key(user_id, normalized)
    if cache is not None:
        try:
            cached = cache.get_sync(key)
        except Exception as exc:  # noqa: BLE001 - cache miss degrades to store
            logger.warning("recall cache degraded: %s", exc)
            cached = None
        if cached:
            return cached
    try:
        recall = format_recall(
            recall_texts(
                store.search(memory_namespace(user_id), query=normalized, limit=DEFAULT_TOP_K)
            )
        )
    except Exception as exc:  # noqa: BLE001 - store miss degrades to empty
        logger.warning("recall degraded: %s", exc)
        return ""
    if recall and cache is not None:
        try:
            cache.set_sync(key, recall, ttl)
        except Exception as exc:  # noqa: BLE001 - recall stands without cache
            logger.warning("recall cache degraded: %s", exc)
    return recall


async def _recall_async(
    store: BaseStore, cache: RecallCachePort | None, ttl: int, user_id: str, query: str
) -> str:
    """Load per-user recall on the async path; empty means inject nothing."""
    normalized = normalize_query(query)
    key = build_memory_key(user_id, normalized)
    if cache is not None:
        try:
            cached = await cache.get(key)
        except Exception as exc:  # noqa: BLE001 - cache miss degrades to store
            logger.warning("recall cache degraded: %s", exc)
            cached = None
        if cached:
            return cached
    try:
        hits = await store.asearch(memory_namespace(user_id), query=normalized, limit=DEFAULT_TOP_K)
        recall = format_recall(recall_texts(hits))
    except Exception as exc:  # noqa: BLE001 - store miss degrades to empty
        logger.warning("recall degraded: %s", exc)
        return ""
    if recall and cache is not None:
        try:
            await cache.set(key, recall, ttl)
        except Exception as exc:  # noqa: BLE001 - recall stands without cache
            logger.warning("recall cache degraded: %s", exc)
    return recall


AgentGraph = CompiledStateGraph[Any, Any, Any, Any]


def make_the_agent(
    checkpointer: BaseCheckpointSaver[str] | None = None,
    *,
    store: BaseStore | None = None,
    cache: RecallCachePort | None = None,
    cache_ttl_seconds: int = 60,
) -> AgentGraph:
    """Compile the echo loop with recall resolved through the bound store.

    The lifespan owns the checkpointer plus the store plus the cache and
    injects them here, so the loop never imports outside layers directly.
    Empty caller id means no recall for that turn. A persisted graph needs
    the async serving path when its checkpointer is async-only.
    """

    def echo_sync(state: GraphState, config: RunnableConfig | None = None) -> dict[str, list[Any]]:
        text = _last_human_text(state.get("messages", []))
        reply = AIMessage(content=f"Echo: {text}")
        user_id = _request_user_id(config)
        recall = ""
        if user_id:
            try:
                recall = _recall_sync(get_store(), cache, cache_ttl_seconds, user_id, text)
            except Exception as exc:  # noqa: BLE001 - recall miss keeps reply
                logger.warning("recall degraded: %s", exc)
                recall = ""
        if recall.strip():
            return {"messages": [reply], "docs": [recall]}
        return {"messages": [reply]}

    async def echo_async(
        state: GraphState, config: RunnableConfig | None = None
    ) -> dict[str, list[Any]]:
        text = _last_human_text(state.get("messages", []))
        user_id = _request_user_id(config)
        recall = ""
        if user_id:
            try:
                recall = await _recall_async(get_store(), cache, cache_ttl_seconds, user_id, text)
            except Exception as exc:  # noqa: BLE001 - recall miss keeps reply
                logger.warning("recall degraded: %s", exc)
                recall = ""
        reply = AIMessage(content=f"Echo: {text}")
        if recall.strip():
            return {"messages": [reply], "docs": [recall]}
        return {"messages": [reply]}

    builder = StateGraph(GraphState)
    builder.add_node("echo", RunnableLambda(echo_sync, afunc=echo_async))
    builder.add_edge(START, "echo")
    builder.add_edge("echo", END)
    return builder.compile(checkpointer=checkpointer, store=store)


graph = make_the_agent()
