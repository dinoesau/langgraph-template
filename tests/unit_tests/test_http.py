"""Unit tests for the HTTP skeleton.

TestClient runs the app in-process: no network and no containers.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver

from agent.graph import make_the_agent
from api.app import create_app


@pytest.fixture(name="client")
def client_fixture() -> TestClient:
    return TestClient(create_app())


def test_health_reports_readiness(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "checks": {"graph": "loaded", "settings": "loaded"},
    }
    assert response.headers["X-Request-ID"]


def test_chat_delegates_to_loop_for_invoke(client: TestClient) -> None:
    response = client.post("/api/v1/chat", json={"message": "hello"})
    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == "Echo: hello"
    assert body["thread_id"]
    assert response.headers["X-Request-ID"]


def test_chat_streams_loop_reply(client: TestClient) -> None:
    with client.stream("POST", "/api/v1/chat/stream", json={"message": "hi"}) as response:
        assert response.status_code == 200
        assert response.headers["X-Request-ID"]
        text = "".join(response.iter_text())
    assert "Echo: hi" in text


def test_chat_rejects_blank_message(client: TestClient) -> None:
    response = client.post("/api/v1/chat", json={"message": "   "})
    assert response.status_code == 422


@asynccontextmanager
async def memory_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Test double for agent_lifespan: same contract, in-memory threads."""
    app.state.agent_graph = make_the_agent(checkpointer=MemorySaver())
    yield


def test_chat_resumes_thread() -> None:
    app = create_app(lifespan=memory_lifespan)
    with TestClient(app) as client:
        first = client.post("/api/v1/chat", json={"message": "one", "thread_id": "t1"})
        second = client.post("/api/v1/chat", json={"message": "two", "thread_id": "t1"})
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["thread_id"] == "t1"
    state = app.state.agent_graph.get_state({"configurable": {"thread_id": "t1"}})
    assert [m.content for m in state.values["messages"]] == ["one", "Echo: one", "two", "Echo: two"]


def test_chat_isolates_threads() -> None:
    with TestClient(create_app(lifespan=memory_lifespan)) as client:
        client.post("/api/v1/chat", json={"message": "one", "thread_id": "a"})
        other = client.post("/api/v1/chat", json={"message": "two", "thread_id": "b"})
    assert other.json()["reply"] == "Echo: two"
