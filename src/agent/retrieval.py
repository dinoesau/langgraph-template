"""Grounded retrieval: top chunks for the last user message, unknown otherwise.

Retrieve refreshes ``passages`` on every turn from the shared policy
namespace, never from per-user memory, so recall can never override
grounding. Generate cites only retrieved passages and answers unknown
when nothing supports an answer, so off-policy questions never hallucinate.
"""

import logging
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_store

from agent.nodes import last_human_text
from agent.state import GraphState
from core.memory import PASSAGE_TOP_K, passage_namespace, recall_texts

logger = logging.getLogger("agent")

UNKNOWN = "I don't know based on the available policy passages."


def _retrieve_texts(store: Any, query: str) -> list[str]:
    """Search the shared policy namespace, degrading to empty on failure."""
    try:
        hits = store.search(passage_namespace(), query=query, limit=PASSAGE_TOP_K)
    except Exception as exc:  # noqa: BLE001 - retrieval miss yields unknown
        logger.warning("retrieval degraded: %s", exc)
        return []
    return recall_texts(hits)


def _retrieve_sync(state: GraphState) -> dict[str, list[Any]]:
    try:
        text = last_human_text(state.get("messages", []))
    except ValueError:
        return {"passages": []}
    try:
        store = get_store()
    except Exception as exc:  # noqa: BLE001 - no store bound, skip retrieval
        logger.warning("retrieval degraded: %s", exc)
        return {"passages": []}
    if store is None:
        return {"passages": []}
    return {"passages": [item.strip() for item in _retrieve_texts(store, text) if item.strip()]}


async def _retrieve_async(state: GraphState) -> dict[str, list[Any]]:
    try:
        text = last_human_text(state.get("messages", []))
    except ValueError:
        return {"passages": []}
    try:
        store = get_store()
    except Exception as exc:  # noqa: BLE001 - no store bound, skip retrieval
        logger.warning("retrieval degraded: %s", exc)
        return {"passages": []}
    if store is None:
        return {"passages": []}
    try:
        hits = await store.asearch(passage_namespace(), query=text, limit=PASSAGE_TOP_K)
        texts = recall_texts(hits)
    except Exception as exc:  # noqa: BLE001 - store miss keeps the reply
        logger.warning("retrieval degraded: %s", exc)
        return {"passages": []}
    return {"passages": [item.strip() for item in texts if item.strip()]}


def _grounded_messages(passages: list[str], question: str, recall: str) -> list[BaseMessage]:
    """Policy passages as data, recall as extra context, question untouched.

    Passages and recall arrive inside explicit boundary tags so retrieved
    text can never read as instructions; only the fixed system sentence
    instructs, and the user question travels as a human message.
    """
    cited = "\n".join(f"[{index + 1}] {chunk}" for index, chunk in enumerate(passages))
    prompt = (
        "Answer using only the policy passages below. Cite the passage "
        "numbers you rely on.\n<passages>\n" + cited + "\n</passages>"
    )
    if recall.strip():
        prompt += "\n<extra_context>\n" + recall.strip() + "\n</extra_context>"
    return [SystemMessage(content=prompt), HumanMessage(content=question)]


def _as_ai(result: Any) -> AIMessage:
    """Normalize the grounded reply; non-chat results stringify at the edge."""
    if isinstance(result, AIMessage):
        return result
    return AIMessage(content=str(result))


def build_retrieve_sync() -> Any:
    """Return the sync retrieve node; refreshes passages once per turn."""

    def retrieve_sync(
        state: GraphState, config: RunnableConfig | None = None
    ) -> dict[str, list[Any]]:
        return _retrieve_sync(state)

    return retrieve_sync


def build_retrieve_async() -> Any:
    """Return the async retrieve node; refreshes passages once per turn."""

    async def retrieve_async(
        state: GraphState, config: RunnableConfig | None = None
    ) -> dict[str, list[Any]]:
        return await _retrieve_async(state)

    return retrieve_async


def build_generate_sync(llm: Any) -> Any:
    """Return the sync generate node; unknown when passages are empty."""

    def generate_sync(
        state: GraphState, config: RunnableConfig | None = None
    ) -> dict[str, list[Any]]:
        text = last_human_text(state.get("messages", []))
        passages = [chunk.strip() for chunk in (state.get("passages") or []) if chunk.strip()]
        if not passages:
            return {"messages": [AIMessage(content=UNKNOWN)]}
        if llm is None:
            return {"messages": [AIMessage(content=f"Echo: {text}")]}
        recall = (state.get("docs") or [""])[0] if state.get("docs") else ""
        try:
            result = llm.call(_grounded_messages(passages, text, recall))
        except Exception as exc:  # noqa: BLE001 - model failure yields unknown
            logger.warning("generate degraded: %s", exc)
            return {"messages": [AIMessage(content=UNKNOWN)]}
        return {"messages": [_as_ai(result)]}

    return generate_sync


def build_generate_async(llm: Any) -> Any:
    """Return the async generate node; unknown when passages are empty."""

    async def generate_async(
        state: GraphState, config: RunnableConfig | None = None
    ) -> dict[str, list[Any]]:
        text = last_human_text(state.get("messages", []))
        passages = [chunk.strip() for chunk in (state.get("passages") or []) if chunk.strip()]
        if not passages:
            return {"messages": [AIMessage(content=UNKNOWN)]}
        if llm is None:
            return {"messages": [AIMessage(content=f"Echo: {text}")]}
        recall = (state.get("docs") or [""])[0] if state.get("docs") else ""
        try:
            result = await llm.acall(_grounded_messages(passages, text, recall))
        except Exception as exc:  # noqa: BLE001 - model failure yields unknown
            logger.warning("generate degraded: %s", exc)
            return {"messages": [AIMessage(content=UNKNOWN)]}
        return {"messages": [_as_ai(result)]}

    return generate_async
