import json
import logging
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel, Field

from src.agent.graph import graph
from src.api.deps import require_session_claims, session_id_from
from src.core.security import TokenClaims
from src.services.database import get_agent_graph
from src.services.memory import get_memory_service, remember_in_background

logger = logging.getLogger(__name__)

router = APIRouter()


class ChatRequest(BaseModel):
    """Inline chat payload, kept in the route module for S1."""

    message: str = Field(min_length=1, max_length=4000)
    thread_id: str | None = Field(default=None, max_length=64)
    user_id: str | None = Field(default=None, max_length=64)


class ChatResponse(BaseModel):
    """Inline chat reply with pass-through thread identifier."""

    reply: str
    thread_id: str


def _resolve_session_thread(thread_id: str | None, claims: TokenClaims) -> str:
    """Bind the chat thread to the session token; reject body mismatches."""
    session_id = session_id_from(claims)
    if thread_id is not None and thread_id != session_id:
        raise HTTPException(status_code=403, detail="Thread does not match the session token.")
    return session_id


def _recall_context(user_id: str, message: str, request: Request | None = None) -> str:
    # Identity comes from the session token, so recall is always scoped to the
    # token owner and memories can never leak across users.
    if request is None or not user_id:
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
    user_id: str = "",
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
    payload: ChatRequest,
    request: Request,
    background: BackgroundTasks,
    claims: Annotated[TokenClaims, Depends(require_session_claims)],
) -> ChatResponse:
    """Invoke the loop and return the reply, persisted when the store is ready."""
    thread_id = _resolve_session_thread(payload.thread_id, claims)
    user_id = claims["sub"]
    reply = await _invoke_reply(payload.message, thread_id, request, user_id)
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
    user_id: str = "",
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
    if request is not None and user_id:
        remember_in_background(get_memory_service(request.app), user_id, message, reply)
    yield f"data: {json.dumps({'delta': reply, 'thread_id': thread_id})}\n\n"
    yield "data: [DONE]\n\n"


@router.post("/chat/stream")
async def chat_stream(
    payload: ChatRequest,
    request: Request,
    claims: Annotated[TokenClaims, Depends(require_session_claims)],
) -> StreamingResponse:
    """Stream the reply as server-sent events, persisted when the store is ready."""
    thread_id = _resolve_session_thread(payload.thread_id, claims)
    user_id = claims["sub"]
    return StreamingResponse(
        _stream_events(payload.message, thread_id, request, user_id),
        media_type="text/event-stream",
    )
