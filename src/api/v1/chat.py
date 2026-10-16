import json
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel, Field

from src.agent.graph import graph
from src.services.database import get_agent_graph

router = APIRouter()


class ChatRequest(BaseModel):
    """Inline chat payload, kept in the route module for S1."""

    message: str = Field(min_length=1, max_length=4000)
    thread_id: str | None = Field(default=None, max_length=64)


class ChatResponse(BaseModel):
    """Inline chat reply with pass-through thread identifier."""

    reply: str
    thread_id: str


def _resolve_thread_id(thread_id: str | None, request: Request | None = None) -> str:
    if thread_id:
        return thread_id
    if request is not None:
        state_id = getattr(request.state, "thread_id", None)
        if isinstance(state_id, str) and state_id:
            return state_id
    return uuid.uuid4().hex


def _invoke_reply(message: str, thread_id: str, request: Request | None = None) -> str:
    active = get_agent_graph(request.app) if request is not None else graph
    result = active.invoke(
        {"messages": [HumanMessage(content=message)]},
        config={"configurable": {"thread_id": thread_id}},
    )
    messages = result.get("messages", [])
    if not messages:
        return ""
    content = messages[-1].content
    if isinstance(content, str):
        return content
    return str(content)


@router.post("/chat", response_model=ChatResponse)
def chat_invoke(payload: ChatRequest, request: Request) -> ChatResponse:
    """Invoke the loop and return the reply, persisted when the store is ready."""
    thread_id = _resolve_thread_id(payload.thread_id, request)
    reply = _invoke_reply(payload.message, thread_id, request)
    return ChatResponse(reply=reply, thread_id=thread_id)


async def _stream_events(
    message: str, thread_id: str, request: Request | None = None
) -> AsyncIterator[str]:
    active = get_agent_graph(request.app) if request is not None else graph
    deltas: list[str] = []
    for update in active.stream(
        {"messages": [HumanMessage(content=message)]},
        config={"configurable": {"thread_id": thread_id}},
    ):
        for node_update in update.values():
            if not isinstance(node_update, dict):
                continue
            for item in node_update.get("messages", []):
                if isinstance(item, AIMessage) and isinstance(item.content, str) and item.content:
                    deltas.append(item.content)
    reply = "".join(deltas)
    if not reply:
        reply = _invoke_reply(message, thread_id, request)
    yield f"data: {json.dumps({'delta': reply, 'thread_id': thread_id})}\n\n"
    yield "data: [DONE]\n\n"


@router.post("/chat/stream")
async def chat_stream(payload: ChatRequest, request: Request) -> StreamingResponse:
    """Stream the reply as server-sent events, persisted when the store is ready."""
    thread_id = _resolve_thread_id(payload.thread_id, request)
    return StreamingResponse(
        _stream_events(payload.message, thread_id, request),
        media_type="text/event-stream",
    )
