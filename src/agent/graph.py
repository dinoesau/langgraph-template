"""Tool loop: chat to tool to chat until no tool calls remain.

Without an llm the chat node echoes, so fresh clones stay green with no
keys and no network. With an llm the same wiring runs the booking tools
through the model fallback, which retries per model and walks the registry
circularly once. Recall runs once per turn before chat, never per iteration.
"""

import logging
from typing import Any

from langchain_core.runnables import RunnableLambda
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode
from langgraph.store.base import BaseStore

from agent.nodes import (
    build_chat_async,
    build_chat_sync,
    build_recall_async,
    build_recall_sync,
)
from agent.routes import should_continue
from agent.state import GraphState
from agent.tools import tools
from core.memory import RecallCachePort

logger = logging.getLogger("agent")

__all__ = ["AgentGraph", "RecallCachePort", "graph", "make_the_agent"]

AgentGraph = CompiledStateGraph[Any, Any, Any, Any]


def make_the_agent(
    checkpointer: BaseCheckpointSaver[str] | None = None,
    *,
    store: BaseStore | None = None,
    cache: RecallCachePort | None = None,
    cache_ttl_seconds: int = 60,
    llm: Any | None = None,
) -> AgentGraph:
    """Compile the loop with recall plus optional model fallback.

    The lifespan owns the checkpointer plus the store plus the cache and
    injects them here, so the loop never imports outside layers directly.
    Empty caller id means no recall for that turn. Empty llm keeps the echo
    fallback so unit tests and fresh clones run with no keys. A persisted
    graph needs the async serving path when its checkpointer is async-only.
    """

    recall_sync = build_recall_sync(cache, cache_ttl_seconds)
    recall_async = build_recall_async(cache, cache_ttl_seconds)
    chat_sync = build_chat_sync(llm, tools)
    chat_async = build_chat_async(llm, tools)

    builder = StateGraph(GraphState)
    builder.add_node("recall", RunnableLambda(recall_sync, afunc=recall_async))
    builder.add_node("chat", RunnableLambda(chat_sync, afunc=chat_async))
    builder.add_node("tools", ToolNode(tools))
    builder.add_edge(START, "recall")
    builder.add_edge("recall", "chat")
    builder.add_conditional_edges("chat", should_continue, {"tools": "tools", "__end__": END})
    builder.add_edge("tools", "chat")
    return builder.compile(checkpointer=checkpointer, store=store)


graph = make_the_agent()
