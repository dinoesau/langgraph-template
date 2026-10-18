"""Echo loop: proves the deploy contract with no model and no storage."""

from collections.abc import Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.graph import END, START, StateGraph

from agent.state import GraphState


def _last_human_text(messages: Sequence[BaseMessage]) -> str:
    """Return the last non-blank human text or raise ValueError."""
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            content = message.content
            if isinstance(content, str) and content.strip():
                return content
    raise ValueError("state must carry a non-empty human message")


def echo(state: GraphState) -> dict[str, list[AIMessage]]:
    """Append one assistant message echoing the last human text."""
    text = _last_human_text(state.get("messages", []))
    return {"messages": [AIMessage(content=f"Echo: {text}")]}


builder = StateGraph(GraphState)
builder.add_node("echo", echo)
builder.add_edge(START, "echo")
builder.add_edge("echo", END)
graph = builder.compile()
