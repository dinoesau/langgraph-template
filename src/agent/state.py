from typing import NotRequired

from langgraph.graph import MessagesState


class GraphState(MessagesState):
    """Single source of truth for the S0 echo loop plus S5a RAG docs."""

    docs: NotRequired[list[str]]
