"""Unit tests for the HTTP layer with session-scoped chat.

TestClient runs the app in-process: no network and no containers.
Auth uses an in-memory SQLite store plus MemorySaver threads per app.
"""

from collections.abc import AsyncGenerator, Iterator
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver
from langgraph.store.memory import InMemoryStore

from agent.graph import make_the_agent
from api.app import create_app
from core.cache import build_cache
from core.config import get_settings


@asynccontextmanager
async def auth_memory_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Test lifespan: MemorySaver threads plus in-memory auth rows."""
    from models.base import Base
    from services.auth_store import auth_session_factory, create_auth_engine

    settings = get_settings()
    store = InMemoryStore()
    cache = build_cache("")
    app.state.agent_store = store
    app.state.recall_cache = cache
    app.state.agent_graph = make_the_agent(
        checkpointer=MemorySaver(),
        store=store,
        cache=cache,
        cache_ttl_seconds=settings.cache_ttl_seconds,
    )
    auth_engine = create_auth_engine("sqlite:///:memory:")
    async with auth_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    app.state.auth_engine = auth_engine
    app.state.auth_session_factory = auth_session_factory(auth_engine)
    try:
        yield
    finally:
        cache.close_sync()
        await cache.aclose()
        await auth_engine.dispose()


@pytest.fixture(name="client")
def client_fixture(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, FastAPI]]:
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-0123456789abcdef01234567")
    monkeypatch.setenv("POSTGRES_URI", "")
    get_settings.cache_clear()
    app = create_app(lifespan=auth_memory_lifespan)
    with TestClient(app) as client:
        yield client, app
    get_settings.cache_clear()


def _user_token(http: TestClient, email: str = "a@b.co") -> str:
    response = http.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "ChangeMe123!", "username": "you"},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["access_token"])


def _session_token(http: TestClient, user_token: str) -> tuple[str, str]:
    response = http.post("/api/v1/auth/session", headers={"Authorization": f"Bearer {user_token}"})
    assert response.status_code == 201, response.text
    body = response.json()
    return str(body["session_id"]), str(body["token"])


def _auth_headers(http: TestClient, email: str = "a@b.co") -> tuple[dict[str, str], str]:
    user_token = _user_token(http, email)
    session_id, session_token = _session_token(http, user_token)
    return {"Authorization": f"Bearer {session_token}"}, session_id


def test_health_reports_readiness(client: tuple[TestClient, FastAPI]) -> None:
    http, _ = client
    response = http.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "checks": {"graph": "loaded", "settings": "loaded"},
    }
    assert response.headers["X-Request-ID"]


def test_chat_requires_auth(client: tuple[TestClient, FastAPI]) -> None:
    http, _ = client
    assert http.post("/api/v1/chat", json={"message": "hello"}).status_code == 401


def test_chat_rejects_user_token(client: tuple[TestClient, FastAPI]) -> None:
    http, _ = client
    user_token = _user_token(http)
    response = http.post(
        "/api/v1/chat",
        json={"message": "hello"},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 403


def test_chat_delegates_to_loop_for_invoke(client: tuple[TestClient, FastAPI]) -> None:
    http, _ = client
    headers, session_id = _auth_headers(http)
    response = http.post("/api/v1/chat", json={"message": "hello"}, headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == "Echo: hello"
    assert body["thread_id"] == session_id
    assert response.headers["X-Request-ID"]


def test_chat_streams_loop_reply(client: tuple[TestClient, FastAPI]) -> None:
    http, _ = client
    headers, _ = _auth_headers(http)
    with http.stream(
        "POST", "/api/v1/chat/stream", json={"message": "hi"}, headers=headers
    ) as response:
        assert response.status_code == 200
        assert response.headers["X-Request-ID"]
        text = "".join(response.iter_text())
    assert "Echo: hi" in text


def test_chat_rejects_blank_message(client: tuple[TestClient, FastAPI]) -> None:
    http, _ = client
    headers, _ = _auth_headers(http)
    response = http.post("/api/v1/chat", json={"message": "   "}, headers=headers)
    assert response.status_code == 422


def test_chat_resumes_thread(client: tuple[TestClient, FastAPI]) -> None:
    http, app = client
    headers, session_id = _auth_headers(http)
    first = http.post("/api/v1/chat", json={"message": "one"}, headers=headers)
    second = http.post("/api/v1/chat", json={"message": "two"}, headers=headers)
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["thread_id"] == session_id
    state = app.state.agent_graph.get_state({"configurable": {"thread_id": session_id}})
    assert [m.content for m in state.values["messages"]] == ["one", "Echo: one", "two", "Echo: two"]


def test_chat_isolates_threads(client: tuple[TestClient, FastAPI]) -> None:
    http, _ = client
    headers_a, _ = _auth_headers(http, "a@b.co")
    headers_b, _ = _auth_headers(http, "b@b.co")
    http.post("/api/v1/chat", json={"message": "one"}, headers=headers_a)
    other = http.post("/api/v1/chat", json={"message": "two"}, headers=headers_b)
    assert other.json()["reply"] == "Echo: two"


def test_chat_stream_resumes_thread(client: tuple[TestClient, FastAPI]) -> None:
    http, app = client
    headers, session_id = _auth_headers(http)
    with http.stream(
        "POST", "/api/v1/chat/stream", json={"message": "one"}, headers=headers
    ) as first:
        assert first.status_code == 200
        assert "Echo: one" in "".join(first.iter_text())
    second = http.post("/api/v1/chat", json={"message": "two"}, headers=headers)
    assert second.status_code == 200
    state = app.state.agent_graph.get_state({"configurable": {"thread_id": session_id}})
    assert [m.content for m in state.values["messages"]] == ["one", "Echo: one", "two", "Echo: two"]


def test_full_journey_register_login_session_chat_history(
    client: tuple[TestClient, FastAPI],
) -> None:
    http, app = client
    created = http.post(
        "/api/v1/auth/register",
        json={"email": "j@b.co", "password": "ChangeMe123!", "username": "jay"},
    )
    assert created.status_code == 201
    logged = http.post(
        "/api/v1/auth/login",
        data={"username": "j@b.co", "password": "ChangeMe123!", "grant_type": "password"},
    )
    assert logged.status_code == 200
    user_token = str(logged.json()["access_token"])
    session_id, session_token = _session_token(http, user_token)
    headers = {"Authorization": f"Bearer {session_token}"}
    first = http.post("/api/v1/chat", json={"message": "one"}, headers=headers)
    second = http.post("/api/v1/chat", json={"message": "two"}, headers=headers)
    assert first.status_code == 200
    assert second.status_code == 200
    listed = http.get("/api/v1/auth/sessions", headers={"Authorization": f"Bearer {user_token}"})
    assert [row["id"] for row in listed.json()] == [session_id]
    state = app.state.agent_graph.get_state({"configurable": {"thread_id": session_id}})
    assert [m.content for m in state.values["messages"]] == ["one", "Echo: one", "two", "Echo: two"]
