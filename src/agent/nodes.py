"""Loop nodes: recall once per turn, chat via fallback, tools via ToolNode.

Recall runs as its own node before the chat loop so vector search happens
exactly once per turn. The chat node never re-queries the store and never
appends ``docs`` per iteration, which keeps checkpoints bounded.
"""

import logging
from collections.abc import Sequence
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_store
from langgraph.store.base import BaseStore

from agent.state import GraphState
from core.memory import (
    DEFAULT_TOP_K,
    RecallCachePort,
    build_memory_key,
    format_recall,
    memory_namespace,
    normalize_query,
    recall_texts,
    sanitize_tag_content,
    valid_user_id,
)

logger = logging.getLogger("agent")


def last_human_text(messages: Sequence[BaseMessage]) -> str:
    """Return the last non-blank human text or raise ValueError."""
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            content = message.content
            if isinstance(content, str) and content.strip():
                return content
    raise ValueError("state must carry a non-empty human message")


def request_user_id(config: RunnableConfig | None) -> str:
    """Read the transitional caller id from configurable without failing."""
    if config is None:
        return ""
    configurable = config.get("configurable") or {}
    raw = configurable.get("user_id", "")
    return valid_user_id(raw) if isinstance(raw, str) else ""


def _recall_key(user_id: str, query: str) -> tuple[str, str]:
    """Scoped cache key plus normalized query; one vocabulary for both paths."""
    normalized = normalize_query(query)
    return build_memory_key(user_id, normalized), normalized


def _recall_inputs(state: GraphState, config: RunnableConfig | None) -> tuple[str, str] | None:
    """Human text plus user id, or None when recall must be skipped this turn."""
    try:
        text = last_human_text(state.get("messages", []))
    except ValueError:
        return None
    user_id = request_user_id(config)
    if not user_id:
        return None
    return text, user_id


def _recall_sync(
    store: BaseStore, cache: RecallCachePort | None, ttl: int, user_id: str, query: str
) -> str:
    key, normalized = _recall_key(user_id, query)
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
    key, normalized = _recall_key(user_id, query)
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


def _chat_call_messages(state: GraphState) -> list[BaseMessage]:
    """History plus recall as ephemeral context for this call only.

    Passages are excluded on purpose: only the retrieval branch runs
    retrieve, so any passages visible here are stale from a prior turn.
    Recall is sanitized so closing tags stay data, never instructions.
    """
    call_messages: list[BaseMessage] = list(state.get("messages", []))
    docs = state.get("docs") or []
    recall = docs[0] if docs else ""
    context: list[BaseMessage] = []
    if recall.strip():
        safe = sanitize_tag_content(recall.strip())
        context = [SystemMessage(content=f"<memory>\n{safe}\n</memory>")]
    return context + call_messages


def _wrap_ai(result: Any) -> AIMessage:
    """Normalize the fallback reply; non-chat results stringify at the edge."""
    if isinstance(result, AIMessage):
        return result
    return AIMessage(content=str(result))


def _clear_docs_if_stale(state: GraphState) -> dict[str, list[Any]]:
    """Clear recalled context only when stale exists; absent stays absent."""
    if state.get("docs"):
        return {"docs": []}
    return {"messages": []}


def build_recall_sync(cache: RecallCachePort | None, ttl: int) -> Any:
    """Return the sync recall node; runs once per turn before chat."""

    def recall_sync(
        state: GraphState, config: RunnableConfig | None = None
    ) -> dict[str, list[Any]]:
        inputs = _recall_inputs(state, config)
        if inputs is None:
            return _clear_docs_if_stale(state)
        text, user_id = inputs
        try:
            recall = _recall_sync(get_store(), cache, ttl, user_id, text)
        except Exception as exc:  # noqa: BLE001 - recall miss keeps reply
            logger.warning("recall degraded: %s", exc)
            return _clear_docs_if_stale(state)
        if recall.strip():
            return {"docs": [recall]}
        return _clear_docs_if_stale(state)

    return recall_sync


def build_recall_async(cache: RecallCachePort | None, ttl: int) -> Any:
    """Return the async recall node; runs once per turn before chat."""

    async def recall_async(
        state: GraphState, config: RunnableConfig | None = None
    ) -> dict[str, list[Any]]:
        inputs = _recall_inputs(state, config)
        if inputs is None:
            return _clear_docs_if_stale(state)
        text, user_id = inputs
        try:
            recall = await _recall_async(get_store(), cache, ttl, user_id, text)
        except Exception as exc:  # noqa: BLE001 - recall miss keeps reply
            logger.warning("recall degraded: %s", exc)
            return _clear_docs_if_stale(state)
        if recall.strip():
            return {"docs": [recall]}
        return _clear_docs_if_stale(state)

    return recall_async


def _stale_passages(state: GraphState) -> dict[str, list[str]]:
    """Clear retrieval output on non-retrieval entry; absent stays absent."""
    if state.get("passages"):
        return {"passages": []}
    return {}


def build_chat_sync(llm: Any, tool_list: Sequence[Any]) -> Any:
    """Return the sync chat node; echo fallback keeps fresh clones green."""

    def chat_sync(state: GraphState, config: RunnableConfig | None = None) -> dict[str, list[Any]]:
        text = last_human_text(state.get("messages", []))
        if llm is None:
            return {"messages": [AIMessage(content=f"Echo: {text}")], **_stale_passages(state)}
        try:
            reply = llm.call(_chat_call_messages(state), list(tool_list) or None)
        except Exception as exc:  # noqa: BLE001 - model failure degrades to echo
            logger.warning("chat degraded: %s", exc)
            return {
                "messages": [AIMessage(content=f"Echo: {text}")],
                **_stale_passages(state),
            }
        return {
            "messages": [_wrap_ai(reply)],
            **_stale_passages(state),
        }

    return chat_sync


def build_chat_async(llm: Any, tool_list: Sequence[Any]) -> Any:
    """Return the async chat node; echo fallback keeps fresh clones green."""

    async def chat_async(
        state: GraphState, config: RunnableConfig | None = None
    ) -> dict[str, list[Any]]:
        text = last_human_text(state.get("messages", []))
        if llm is None:
            return {"messages": [AIMessage(content=f"Echo: {text}")], **_stale_passages(state)}
        try:
            result = await llm.acall(_chat_call_messages(state), list(tool_list) or None)
        except Exception as exc:  # noqa: BLE001 - model failure degrades to echo
            logger.warning("chat degraded: %s", exc)
            return {
                "messages": [AIMessage(content=f"Echo: {text}")],
                **_stale_passages(state),
            }
        return {"messages": [_wrap_ai(result)], **_stale_passages(state)}

    return chat_async
