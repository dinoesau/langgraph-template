"""Echo loop: proves the deploy contract with no model and no storage."""

import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig, RunnableLambda
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agent.state import GraphState

logger = logging.getLogger("agent")

RecallHook = Callable[[str, str], Awaitable[str]]
StoreHook = Callable[[str, list[BaseMessage]], None]


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
    return raw.strip() if isinstance(raw, str) else ""


AgentGraph = CompiledStateGraph[Any, Any, Any, Any]


def make_the_agent(
    checkpointer: BaseCheckpointSaver[str] | None = None,
    *,
    recall_hook: RecallHook | None = None,
    store_hook: StoreHook | None = None,
) -> AgentGraph:
    """Compile the echo loop, persisting per thread only when given a checkpointer.

    The lifespan owns the checkpointer plus the recall hooks and injects
    them here, so the loop never imports outside layers directly.
    Empty caller id means no recall and no write for that turn.
    """

    def echo_sync(state: GraphState, config: RunnableConfig | None = None) -> dict[str, list[Any]]:
        text = _last_human_text(state.get("messages", []))
        reply = AIMessage(content=f"Echo: {text}")
        user_id = _request_user_id(config)
        if store_hook is not None and user_id:
            try:
                store_hook(user_id, [*state.get("messages", []), reply])
            except Exception as exc:  # noqa: BLE001 - hook miss keeps reply
                logger.warning("background write degraded: %s", exc)
        return {"messages": [reply]}

    async def echo_async(
        state: GraphState, config: RunnableConfig | None = None
    ) -> dict[str, list[Any]]:
        text = _last_human_text(state.get("messages", []))
        user_id = _request_user_id(config)
        recall = ""
        if recall_hook is not None and user_id:
            try:
                recall = await recall_hook(user_id, text)
            except Exception as exc:  # noqa: BLE001 - recall miss keeps reply
                logger.warning("recall degraded: %s", exc)
                recall = ""
        reply = AIMessage(content=f"Echo: {text}")
        if store_hook is not None and user_id:
            try:
                store_hook(user_id, [*state.get("messages", []), reply])
            except Exception as exc:  # noqa: BLE001 - hook miss keeps reply
                logger.warning("background write degraded: %s", exc)
        if recall.strip():
            return {"messages": [reply], "docs": [recall]}
        return {"messages": [reply]}

    builder = StateGraph(GraphState)
    builder.add_node("echo", RunnableLambda(echo_sync, afunc=echo_async))
    builder.add_edge(START, "echo")
    builder.add_edge("echo", END)
    return builder.compile(checkpointer=checkpointer)


graph = make_the_agent()
