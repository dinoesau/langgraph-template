from typing import NotRequired

from langgraph.graph import MessagesState


class GraphState(MessagesState):
    """Single source of truth for the chat loop and the S5d grader loop."""

    attempts: NotRequired[int]
    approved: NotRequired[bool]
    feedback: NotRequired[str]
