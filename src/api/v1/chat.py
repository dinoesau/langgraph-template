"""Chat over HTTP: thin translation into loop invoke and stream calls."""

import json
import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel, field_validator
from starlette.background import BackgroundTask

from agent.graph import AgentGraph
from api.deps import WriterHook, get_memory_writer, get_request_graph, get_request_user_id
from services.memory import snapshot_messages

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
    agent: AgentGraph, message: str, thread_id: str, user_id: str, box: dict[str, str]
) -> AsyncIterator[str]:
    """Yield server-sent event chunks and record the full reply for the writer."""
    texts: list[str] = []
    async for chunk in agent.astream(
        {"messages": [HumanMessage(content=message)]},
        config={"configurable": {"thread_id": thread_id, "user_id": user_id}},
        stream_mode="updates",
    ):
        for node_output in chunk.values():
            for msg in node_output.get("messages", []):
                if isinstance(msg, AIMessage) and isinstance(msg.content, str):
                    texts.append(msg.content)
                    yield f"data: {json.dumps({'text': msg.content})}\n\n"
    box["reply"] = "".join(texts)


@router.post("/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    agent: Annotated[AgentGraph, Depends(get_request_graph)],
    user_id: Annotated[str, Depends(get_request_user_id)],
    writer: Annotated[WriterHook, Depends(get_memory_writer)],
    background: BackgroundTasks,
) -> ChatResponse:
    """Delegate one turn to the loop and persist memories after the reply."""
    thread_id = body.thread_id or uuid.uuid4().hex
    reply = await _invoke_loop(agent, body.message, thread_id, user_id)
    background.add_task(writer, user_id, snapshot_messages(body.message, reply))
    return ChatResponse(reply=reply, thread_id=thread_id)


@router.post("/chat/stream")
def chat_stream(
    body: ChatRequest,
    agent: Annotated[AgentGraph, Depends(get_request_graph)],
    user_id: Annotated[str, Depends(get_request_user_id)],
    writer: Annotated[WriterHook, Depends(get_memory_writer)],
) -> StreamingResponse:
    """Delegate one turn to the loop, stream it, then persist memories."""
    thread_id = body.thread_id or uuid.uuid4().hex
    box: dict[str, str] = {}

    async def _write_after() -> None:
        await writer(user_id, snapshot_messages(body.message, box.get("reply")))

    return StreamingResponse(
        _stream_loop(agent, body.message, thread_id, user_id, box),
        media_type="text/event-stream",
        background=BackgroundTask(_write_after),
    )
