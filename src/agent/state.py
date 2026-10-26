"""GraphState: single source of truth for loop state."""

from typing import Literal, NotRequired

from langgraph.graph import MessagesState


class GraphState(MessagesState):
    """Conversational state with a message list plus optional recall context."""

    docs: NotRequired[list[str]]
    passages: NotRequired[list[str]]
    triage_decision: NotRequired[Literal["retrieval", "booking", "chat"]]
    sentiment: NotRequired[str]
    urgency: NotRequired[str]
    category: NotRequired[str]
    ticket_id: NotRequired[str]
    attempts: NotRequired[int]
    grade_feedback: NotRequired[str]
