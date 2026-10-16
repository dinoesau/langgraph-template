"""S3 loop nodes plus the S5d generator/grader evaluator loop."""

import asyncio
import logging
from collections.abc import Mapping
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from pydantic import BaseModel, Field

from src.agent.state import GraphState
from src.agent.tools import tools
from src.services.llm.service import llm_service

logger = logging.getLogger(__name__)

llm_service.bind_tools(tools)

_tools_by_name = {tool.name: tool for tool in tools}

MAX_GRADER_ITERATIONS = 3


class GradeDecision(BaseModel):
    """Validated grader verdict; the LLM must return this shape."""

    approved: bool = Field(description="True when the draft is ready to send.")
    feedback: str = Field(default="", description="Revision hint used on reject.")


async def chat(state: GraphState) -> dict[str, list[BaseMessage]]:
    """Run one model step; the reply may carry tool calls for the next node."""
    response = await llm_service.call(state["messages"])
    if isinstance(response, BaseMessage):
        return {"messages": [response]}
    return {"messages": [AIMessage(content=str(response))]}


async def tool_call(state: GraphState) -> dict[str, list[ToolMessage]]:
    """Run the last reply's tool calls concurrently and feed results back."""
    pending: list[Mapping[str, Any]] = []
    for message in reversed(state.get("messages", [])):
        if isinstance(message, AIMessage) and message.tool_calls:
            pending = list(message.tool_calls)
            break
    results = await asyncio.gather(*(_run_one(call) for call in pending))
    return {"messages": list(results)}


async def _run_one(call: Mapping[str, Any]) -> ToolMessage:
    name = str(call.get("name", ""))
    call_id = str(call.get("id", ""))
    tool = _tools_by_name.get(name)
    if tool is None:
        return ToolMessage(content=f"Unknown tool {name}.", tool_call_id=call_id, name=name)
    try:
        result = await tool.ainvoke(dict(call))
    except Exception:
        logger.exception("Tool %s failed, feeding the failure back to the loop", name)
        return ToolMessage(
            content=f"Tool {name} failed; see logs.", tool_call_id=call_id, name=name
        )
    if isinstance(result, ToolMessage):
        if not result.tool_call_id:
            result = ToolMessage(content=result.content, tool_call_id=call_id, name=name)
        return result
    return ToolMessage(content=str(result), tool_call_id=call_id, name=name)


async def generator(state: GraphState) -> dict[str, Any]:
    """Draft one reply; the grader feedback already sits in messages on retry."""
    response = await llm_service.call(state["messages"])
    attempts = int(state.get("attempts", 0)) + 1
    if isinstance(response, AIMessage):
        draft = response
    elif isinstance(response, BaseMessage):
        draft = AIMessage(content=str(response.content))
    else:
        draft = AIMessage(content=str(response))
    return {"messages": [draft], "attempts": attempts}


async def grader(state: GraphState) -> dict[str, Any]:
    """Check the last draft via validated structured output."""
    decision = await llm_service.call(state["messages"], response_format=GradeDecision)
    if isinstance(decision, GradeDecision):
        approved = decision.approved
        feedback = decision.feedback
    elif isinstance(decision, dict):
        approved = bool(decision.get("approved", False))
        feedback = str(decision.get("feedback", ""))
    else:
        approved = bool(getattr(decision, "approved", False))
        feedback = str(getattr(decision, "feedback", ""))
    if approved:
        return {"approved": True, "feedback": ""}
    hint = feedback.strip() or "Revise the draft to be polite and helpful."
    if int(state.get("attempts", 0)) >= MAX_GRADER_ITERATIONS:
        return {"approved": False, "feedback": hint}
    return {
        "approved": False,
        "feedback": hint,
        "messages": [HumanMessage(content=hint)],
    }
