from langgraph.graph import END, START, StateGraph

from src.agent.nodes import chat
from src.agent.state import GraphState

# S0 keeps a single-node START -> chat -> END chain.
# The planned two-node chat <-> tool_call loop collapses to this echo until S3,
# when tools and routing land, so no checkpointer is wired here.
_builder = StateGraph(GraphState)
_builder.add_node("chat", chat)
_builder.add_edge(START, "chat")
_builder.add_edge("chat", END)

graph = _builder.compile()
