"""GraphState: single source of truth for loop state."""

from typing import NotRequired

from langgraph.graph import MessagesState


class GraphState(MessagesState):
    """Conversational state with a message list plus optional recall context."""

    docs: NotRequired[list[str]]
