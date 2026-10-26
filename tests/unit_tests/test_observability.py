"""Unit tests for S6 observability: traces plus logs plus metrics."""

import json
from collections.abc import AsyncGenerator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.store.memory import InMemoryStore

from agent.graph import make_the_agent
from api.app import create_app
from core.cache import build_cache
from core.config import get_settings
from core.limiter import reset_limiter
from core.middleware import _normalize_path
from services.llm.service import _extract_usage, _resolve_metadata


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
    reset_limiter()
    app = create_app(lifespan=auth_memory_lifespan)
    with TestClient(app) as client:
        yield client, app
    get_settings.cache_clear()


def test_normalize_path_keeps_low_cardinality() -> None:
    assert _normalize_path("/api/v1/auth/register") == "/api/v1/auth/register"
    assert _normalize_path("/api/v1/auth/session/abc123def45678901234567890") == (
        "/api/v1/auth/session/{id}"
    )
    assert _normalize_path("/api/v1/chat") == "/api/v1/chat"


def test_extract_usage_reads_reply_metadata() -> None:
    reply = AIMessage(
        content="ok",
        usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
    )
    assert _extract_usage(reply)["usage_metadata"] == {
        "input_tokens": 1,
        "output_tokens": 1,
        "total_tokens": 2,
    }
    assert _extract_usage(AIMessage(content="plain")) == {}


def test_resolve_metadata_prefers_explicit_over_context() -> None:
    meta = _resolve_metadata({"request_id": "r1", "session_id": "s1"})
    assert meta["request_id"] == "r1"
    assert meta["session_id"] == "s1"


def test_request_id_echo_and_unique(client: tuple[TestClient, object]) -> None:
    http, _ = client
    first = http.get("/api/v1/health")
    second = http.get("/api/v1/health")
    assert first.headers["X-Request-ID"]
    assert second.headers["X-Request-ID"]
    assert first.headers["X-Request-ID"] != second.headers["X-Request-ID"]
    echoed = http.get("/api/v1/health", headers={"X-Request-ID": "fixed-id"})
    assert echoed.headers["X-Request-ID"] == "fixed-id"


def test_metrics_exposes_four_signals(client: tuple[TestClient, object]) -> None:
    http, _ = client
    http.get("/api/v1/health")
    response = http.get("/metrics")
    assert response.status_code == 200
    body = response.text
    assert "http_requests_total" in body
    assert "http_request_duration_seconds" in body
    assert "llm_inference_duration_seconds" in body
    assert "db_connections" in body
    assert 'route="/api/v1/health"' in body


def test_debug_profile_joins_by_request_id(
    client: tuple[TestClient, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEBUG", "true")
    monkeypatch.setenv("SLOW_REQUEST_THRESHOLD_SECONDS", "0")
    get_settings.cache_clear()
    http, _ = client
    response = http.get("/api/v1/health")
    request_id = response.headers["X-Request-ID"]
    profile = Path("profiles") / f"{request_id}.json"
    try:
        assert profile.exists()
        payload = json.loads(profile.read_text())
        assert payload["request_id"] == request_id
    finally:
        if profile.exists():
            profile.unlink()
        get_settings.cache_clear()


def test_llm_trace_records_model_latency_usage() -> None:
    from services.llm.service import LlmService

    class FakeModel:
        def invoke(self, messages: object, config: object = None) -> AIMessage:
            assert config is not None
            assert config["metadata"]["model"] == "m1"  # type: ignore[index]
            return AIMessage(
                content="ok",
                usage_metadata={"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
            )

    service = LlmService(
        [("m1", FakeModel())],
        max_retries=1,
        total_timeout_seconds=10,
        backoff_min_seconds=0,
        backoff_max_seconds=0,
    )
    result = service.call(
        [HumanMessage(content="hi")], metadata={"request_id": "r1", "session_id": "s1"}
    )
    assert isinstance(result, AIMessage)
    assert result.content == "ok"
