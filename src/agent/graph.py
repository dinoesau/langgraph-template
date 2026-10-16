from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from src.agent.nodes import chat, tool_call
from src.agent.routes import route_after_chat
from src.agent.state import GraphState

# S3 runs the two-node chat <-> tool_call ReAct loop.
# The loop ends when the LLM reply carries no tool calls.
# Persistence arrives only through the factory below:
# the loop module never imports storage, it only accepts an injected checkpointer.


def _build() -> StateGraph[GraphState, Any, Any, Any]:
    builder = StateGraph(GraphState)
    builder.add_node("chat", chat)
    builder.add_node("tool_call", tool_call)
    builder.add_edge(START, "chat")
    builder.add_conditional_edges(
        "chat", route_after_chat, {"tool_call": "tool_call", "__end__": END}
    )
    builder.add_edge("tool_call", "chat")
    return builder


def make_the_agent(
    checkpointer: BaseCheckpointSaver[str] | None = None,
) -> CompiledStateGraph[GraphState, Any, Any, Any]:
    """Compile the loop, persisting per thread only when given a checkpointer."""
    return _build().compile(checkpointer=checkpointer)


graph = make_the_agent()
