"""S5a RAG nodes: retrieve top-k for the last user message, then generate."""

from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from src.agent.state import GraphState
from src.services.llm.service import llm_service
from src.services.retrieval import (
    DEFAULT_TOP_K,
    InMemoryRetrieverStore,
    RetrieverStore,
)

UNKNOWN_REPLY = "I don't know."

_default_store = InMemoryRetrieverStore()


async def retrieve(
    state: GraphState,
    *,
    store: RetrieverStore | None = None,
    top_k: int = DEFAULT_TOP_K,
) -> dict[str, list[str]]:
    """Return top-k chunks for the last user message only, never the history."""
    query = ""
    for message in reversed(state.get("messages", [])):
        if isinstance(message, HumanMessage):
            content = message.content
            query = content if isinstance(content, str) else str(content)
            break
    if not query.strip():
        return {"docs": []}
    active = store if store is not None else _default_store
    return {"docs": list(active.search(query, top_k))}


def _last_question(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            content = message.content
            return content if isinstance(content, str) else str(content)
    return ""


async def generate(state: GraphState, *, memory_context: str = "") -> dict[str, list[BaseMessage]]:
    """Answer only from docs, citing them; unknown when unsupported.

    Memory arrives as extra background only and never overrides docs.
    """
    docs = [doc for doc in state.get("docs", []) if doc.strip()]
    question = _last_question(list(state.get("messages", [])))
    if not docs or not question.strip():
        return {"messages": [AIMessage(content=UNKNOWN_REPLY)]}
    passages = "\n".join(f"[{index + 1}] {doc}" for index, doc in enumerate(docs))
    memory_block = memory_context.strip() or "(none)"
    prompt = (
        "Answer ONLY from the PASSAGES below and cite the passage number "
        "for every fact, e.g. [1]. MEMORY is extra background only and "
        f"NEVER overrides the PASSAGES.\nPASSAGES:\n{passages}\n"
        f"MEMORY (extra, never overrides):\n{memory_block}\n"
        'If the PASSAGES do not support the answer, reply exactly "I don\'t know.".'
    )
    request = [SystemMessage(content=prompt), HumanMessage(content=question)]
    response = await llm_service.call(request)
    if isinstance(response, BaseMessage):
        return {"messages": [response]}
    return {"messages": [AIMessage(content=str(response))]}


def make_rag_agent(
    checkpointer: BaseCheckpointSaver[str] | None = None,
) -> CompiledStateGraph[GraphState, Any, Any, Any]:
    """Compile the retrieve -> generate chain sharing GraphState docs."""
    builder = StateGraph(GraphState)
    builder.add_node("retrieve", retrieve)
    builder.add_node("generate", generate)
    builder.add_edge(START, "retrieve")
    builder.add_edge("retrieve", "generate")
    builder.add_edge("generate", END)
    return builder.compile(checkpointer=checkpointer)
