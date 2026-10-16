"""S3 loop nodes: one LLM chat step plus concurrent tool execution."""

import asyncio
import logging
from collections.abc import Mapping
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

from src.agent.state import GraphState
from src.agent.tools import tools
from src.services.llm.service import llm_service

logger = logging.getLogger(__name__)

llm_service.bind_tools(tools)

_tools_by_name = {tool.name: tool for tool in tools}


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
