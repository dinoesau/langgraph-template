"""Chat over HTTP: thin translation into loop invoke and stream calls."""

import json
import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel, field_validator

from agent.graph import AgentGraph
from api.deps import get_request_graph, get_request_user_id

router = APIRouter(tags=["chat"])


class ChatRequest(BaseModel):
    """Inbound chat shape, parsed once at the HTTP edge."""

    message: str
    thread_id: str | None = None

    @field_validator("message")
    @classmethod
    def _strip_and_reject_blank(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("message must not be blank")
        return text


class ChatResponse(BaseModel):
    """Outbound chat shape, no ORM types cross here."""

    reply: str
    thread_id: str


async def _invoke_loop(agent: AgentGraph, message: str, thread_id: str, user_id: str) -> str:
    """Run one loop turn on its thread and return the assistant text."""
    result = await agent.ainvoke(
        {"messages": [HumanMessage(content=message)]},
        config={"configurable": {"thread_id": thread_id, "user_id": user_id}},
    )
    last = result["messages"][-1]
    if isinstance(last, AIMessage) and isinstance(last.content, str):
        return last.content
    raise RuntimeError("loop returned no assistant text")


async def _stream_loop(
    agent: AgentGraph, message: str, thread_id: str, user_id: str
) -> AsyncIterator[str]:
    """Yield server-sent event chunks from one loop turn on its thread."""
    async for chunk in agent.astream(
        {"messages": [HumanMessage(content=message)]},
        config={"configurable": {"thread_id": thread_id, "user_id": user_id}},
        stream_mode="updates",
    ):
        for node_output in chunk.values():
            for msg in node_output.get("messages", []):
                if isinstance(msg, AIMessage) and isinstance(msg.content, str):
                    yield f"data: {json.dumps({'text': msg.content})}\n\n"


@router.post("/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    agent: Annotated[AgentGraph, Depends(get_request_graph)],
    user_id: Annotated[str, Depends(get_request_user_id)],
) -> ChatResponse:
    """Delegate one turn to the loop and return its reply."""
    thread_id = body.thread_id or uuid.uuid4().hex
    return ChatResponse(
        reply=await _invoke_loop(agent, body.message, thread_id, user_id),
        thread_id=thread_id,
    )


@router.post("/chat/stream")
def chat_stream(
    body: ChatRequest,
    agent: Annotated[AgentGraph, Depends(get_request_graph)],
    user_id: Annotated[str, Depends(get_request_user_id)],
) -> StreamingResponse:
    """Delegate one turn to the loop and stream its reply back."""
    thread_id = body.thread_id or uuid.uuid4().hex
    return StreamingResponse(
        _stream_loop(agent, body.message, thread_id, user_id),
        media_type="text/event-stream",
    )
