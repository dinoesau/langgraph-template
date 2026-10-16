from typing import NotRequired

from langgraph.graph import MessagesState


class GraphState(MessagesState):
    """Single source of truth for the chat loop plus S5a RAG docs plus S5d grader loop."""

    docs: NotRequired[list[str]]
    attempts: NotRequired[int]
    approved: NotRequired[bool]
    feedback: NotRequired[str]
