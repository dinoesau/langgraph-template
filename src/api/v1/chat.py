"""Chat over HTTP: thin translation into loop invoke and stream calls."""

import json
import uuid
from collections.abc import Iterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel, field_validator

from agent.graph import graph

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


def _invoke_loop(message: str) -> str:
    """Run one loop turn and return the assistant text."""
    result = graph.invoke({"messages": [HumanMessage(content=message)]})
    last = result["messages"][-1]
    if isinstance(last, AIMessage) and isinstance(last.content, str):
        return last.content
    raise RuntimeError("loop returned no assistant text")


def _stream_loop(message: str) -> Iterator[str]:
    """Yield server-sent event chunks from one loop turn."""
    for chunk in graph.stream({"messages": [HumanMessage(content=message)]}, stream_mode="updates"):
        for node_output in chunk.values():
            for msg in node_output.get("messages", []):
                if isinstance(msg, AIMessage) and isinstance(msg.content, str):
                    yield f"data: {json.dumps({'text': msg.content})}\n\n"


@router.post("/chat", response_model=ChatResponse)
def chat(request: Request, body: ChatRequest) -> ChatResponse:
    """Delegate one turn to the loop and return its reply."""
    thread_id = body.thread_id or uuid.uuid4().hex
    return ChatResponse(reply=_invoke_loop(body.message), thread_id=thread_id)


@router.post("/chat/stream")
def chat_stream(body: ChatRequest) -> StreamingResponse:
    """Delegate one turn to the loop and stream its reply back."""
    return StreamingResponse(_stream_loop(body.message), media_type="text/event-stream")
