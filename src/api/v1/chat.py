import json
import logging
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel, Field

from src.agent.graph import graph
from src.services.database import get_agent_graph
from src.services.memory import get_memory_service, remember_in_background

logger = logging.getLogger(__name__)

router = APIRouter()

ANONYMOUS_USER_ID = "anonymous"


class ChatRequest(BaseModel):
    """Inline chat payload, kept in the route module for S1."""

    message: str = Field(min_length=1, max_length=4000)
    thread_id: str | None = Field(default=None, max_length=64)
    user_id: str | None = Field(default=None, max_length=64)


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


def _resolve_user_id(user_id: str | None, request: Request | None = None) -> str:
    if user_id and user_id.strip():
        return user_id.strip()[:64]
    if request is not None:
        header = request.headers.get("X-User-ID")
        if header and header.strip():
            return header.strip()[:64]
    return ANONYMOUS_USER_ID


def _recall_context(user_id: str, message: str, request: Request | None = None) -> str:
    # Anonymous callers share no identity, so memory stays off for them:
    # recalling under one shared id would leak memories across users.
    if request is None or user_id == ANONYMOUS_USER_ID:
        return ""
    try:
        service = get_memory_service(request.app)
        if service is None:
            return ""
        return service.recall(user_id, message)
    except Exception:
        logger.warning("memory recall failed, replying without context", exc_info=True)
        return ""


def _with_memory_context(message: str, context: str) -> str:
    if not context.strip():
        return message
    return f"Relevant memories:\n{context}\n\nUser: {message}"


async def _invoke_reply(
    message: str,
    thread_id: str,
    request: Request | None = None,
    user_id: str = "anonymous",
) -> str:
    active = get_agent_graph(request.app) if request is not None else graph
    context = _recall_context(user_id, message, request)
    result = await active.ainvoke(
        {"messages": [HumanMessage(content=_with_memory_context(message, context))]},
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
async def chat_invoke(
    payload: ChatRequest, request: Request, background: BackgroundTasks
) -> ChatResponse:
    """Invoke the loop and return the reply, persisted when the store is ready."""
    thread_id = _resolve_thread_id(payload.thread_id, request)
    user_id = _resolve_user_id(payload.user_id, request)
    reply = await _invoke_reply(payload.message, thread_id, request, user_id)
    if user_id != ANONYMOUS_USER_ID:
        background.add_task(
            remember_in_background,
            get_memory_service(request.app),
            user_id,
            payload.message,
            reply,
        )
    return ChatResponse(reply=reply, thread_id=thread_id)


async def _stream_events(
    message: str,
    thread_id: str,
    request: Request | None = None,
    user_id: str = "anonymous",
) -> AsyncIterator[str]:
    active = get_agent_graph(request.app) if request is not None else graph
    context = _recall_context(user_id, message, request)
    effective = _with_memory_context(message, context)
    deltas: list[str] = []
    async for update in active.astream(
        {"messages": [HumanMessage(content=effective)]},
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
        reply = await _invoke_reply(message, thread_id, request, user_id)
    if request is not None and user_id != ANONYMOUS_USER_ID:
        remember_in_background(get_memory_service(request.app), user_id, message, reply)
    yield f"data: {json.dumps({'delta': reply, 'thread_id': thread_id})}\n\n"
    yield "data: [DONE]\n\n"


@router.post("/chat/stream")
async def chat_stream(payload: ChatRequest, request: Request) -> StreamingResponse:
    """Stream the reply as server-sent events, persisted when the store is ready."""
    thread_id = _resolve_thread_id(payload.thread_id, request)
    user_id = _resolve_user_id(payload.user_id, request)
    return StreamingResponse(
        _stream_events(payload.message, thread_id, request, user_id),
        media_type="text/event-stream",
    )
