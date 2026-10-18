"""GraphState: single source of truth for loop state."""

from langgraph.graph import MessagesState


class GraphState(MessagesState):
    """Conversational state with a message list, ephemeral per turn with no checkpointer yet."""
