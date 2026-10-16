from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from src.agent.nodes import chat, generator, grader, tool_call
from src.agent.rag import generate, retrieve
from src.agent.routes import intention_route, route_after_chat, route_after_grader
from src.agent.state import GraphState

# S3 runs the two-node chat <-> tool_call ReAct loop.
# The loop ends when the LLM reply carries no tool calls.
# S5d runs the generator -> grader evaluator loop with a guard below 3.
# S5b routes entry requests to RAG, booking, or chat via intention_route.
# The booking branch reuses the S3 loop as a subgraph sharing GraphState;
# it compiles without a checkpointer so it inherits the parent one.
# Persistence arrives only through the factories below:
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


def _build_evaluator() -> StateGraph[GraphState, Any, Any, Any]:
    builder = StateGraph(GraphState)
    builder.add_node("generator", generator)
    builder.add_node("grader", grader)
    builder.add_edge(START, "generator")
    builder.add_edge("generator", "grader")
    builder.add_conditional_edges(
        "grader", route_after_grader, {"generator": "generator", "__end__": END}
    )
    return builder


def make_evaluator_agent(
    checkpointer: BaseCheckpointSaver[str] | None = None,
) -> CompiledStateGraph[GraphState, Any, Any, Any]:
    """Compile the generator/grader loop with the same checkpointer contract."""
    return _build_evaluator().compile(checkpointer=checkpointer)


def _build_router() -> StateGraph[GraphState, Any, Any, Any]:
    builder = StateGraph(GraphState)
    booking_subgraph = _build().compile()
    builder.add_node("retrieve", retrieve)
    builder.add_node("generate", generate)
    builder.add_node("booking", booking_subgraph)
    builder.add_node("chat", chat)
    builder.add_conditional_edges(
        START,
        intention_route,
        {"rag": "retrieve", "booking": "booking", "chat": "chat"},
    )
    builder.add_edge("retrieve", "generate")
    builder.add_edge("generate", END)
    builder.add_edge("booking", END)
    builder.add_edge("chat", END)
    return builder


def make_router_agent(
    checkpointer: BaseCheckpointSaver[str] | None = None,
) -> CompiledStateGraph[GraphState, Any, Any, Any]:
    """Compile the entry router, persisting per thread only with a checkpointer."""
    return _build_router().compile(checkpointer=checkpointer)


graph = make_the_agent()
evaluator_graph = make_evaluator_agent()
router_graph = make_router_agent()
