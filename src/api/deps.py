"""Request-scoped dependencies: thin readers over process resources."""

from fastapi import Request

from agent.graph import AgentGraph
from services.database import get_agent_graph


def get_request_graph(request: Request) -> AgentGraph:
    """Resolve the loop for this request from what the lifespan attached."""
    return get_agent_graph(request.app)
