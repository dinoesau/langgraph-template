from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from src.agent.nodes import chat
from src.agent.state import GraphState

# S0 keeps a single-node START -> chat -> END chain.
# The planned two-node chat <-> tool_call loop collapses to this echo until S3,
# when tools and routing land. Persistence arrives only through the factory below:
# the loop module never imports storage, it only accepts an injected checkpointer.


def _build() -> StateGraph[GraphState, Any, Any, Any]:
    builder = StateGraph(GraphState)
    builder.add_node("chat", chat)
    builder.add_edge(START, "chat")
    builder.add_edge("chat", END)
    return builder


def make_the_agent(
    checkpointer: BaseCheckpointSaver[str] | None = None,
) -> CompiledStateGraph[GraphState, Any, Any, Any]:
    """Compile the loop, persisting per thread only when given a checkpointer."""
    return _build().compile(checkpointer=checkpointer)


graph = make_the_agent()
