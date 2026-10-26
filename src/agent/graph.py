"""Triage loop: recall to triage to retrieval, booking, or chat.

Without an llm the chat node echoes, so fresh clones stay green with no
keys and no network. With an llm the same wiring runs the booking tools
through the model fallback, which retries per model and walks the registry
circularly once. Recall runs once per turn before triage, never per iteration.
Triage sends refund or policy wording to retrieve plus generate, date or
time wording to the shared chat plus tools loop, and the rest to free chat.
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
from agent.retrieval import (
    build_generate_async,
    build_generate_sync,
    build_retrieve_async,
    build_retrieve_sync,
)
from agent.routes import TRIAGE_TARGETS, should_continue, triage_decision_route, triage_route
from agent.state import GraphState
from agent.tools import tools
from agent.triage import build_triage_assist_async, build_triage_assist_sync
from core.memory import RecallCachePort

logger = logging.getLogger("agent")

__all__ = ["AgentGraph", "RecallCachePort", "graph", "make_the_agent"]

AgentGraph = CompiledStateGraph[Any, Any, Any, Any]

DEFAULT_RECURSION_LIMIT = 25
"""Cap on super-steps per turn; each chat plus tools cycle costs model calls."""


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
    retrieve_sync = build_retrieve_sync()
    retrieve_async = build_retrieve_async()
    generate_sync = build_generate_sync(llm)
    generate_async = build_generate_async(llm)
    assist_sync = build_triage_assist_sync(llm)
    assist_async = build_triage_assist_async(llm)
    chat_sync = build_chat_sync(llm, tools)
    chat_async = build_chat_async(llm, tools)

    builder = StateGraph(GraphState)
    builder.add_node("recall", RunnableLambda(recall_sync, afunc=recall_async))
    builder.add_node("retrieve", RunnableLambda(retrieve_sync, afunc=retrieve_async))
    builder.add_node("generate", RunnableLambda(generate_sync, afunc=generate_async))
    builder.add_node("triage_assist", RunnableLambda(assist_sync, afunc=assist_async))
    builder.add_node("chat", RunnableLambda(chat_sync, afunc=chat_async))
    builder.add_node("tools", ToolNode(tools))
    builder.add_edge(START, "recall")
    builder.add_conditional_edges(
        "recall",
        triage_route,
        {**TRIAGE_TARGETS, "triage_assist": "triage_assist"},
    )
    builder.add_conditional_edges(
        "triage_assist",
        triage_decision_route,
        TRIAGE_TARGETS,
    )
    builder.add_edge("retrieve", "generate")
    builder.add_edge("generate", END)
    builder.add_conditional_edges("chat", should_continue, {"tools": "tools", "__end__": END})
    builder.add_edge("tools", "chat")
    return builder.compile(checkpointer=checkpointer, store=store)


graph = make_the_agent()
