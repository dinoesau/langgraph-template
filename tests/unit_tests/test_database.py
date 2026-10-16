import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage

from src.agent.graph import graph
from src.api.app import create_app
from src.core.config import get_settings
from src.services.database import (
    STORE_NOT_CONFIGURED,
    STORE_UNAVAILABLE,
    database_lifespan,
    get_agent_graph,
    store_status,
)


def test_health_reports_store_not_configured_by_default() -> None:
    client = TestClient(create_app())
    body = client.get("/health").json()
    assert body["dependencies"]["store"] == STORE_NOT_CONFIGURED


def test_health_reflects_lifespan_store_status() -> None:
    app = create_app()
    app.state.store_status = "ready"
    client = TestClient(app)
    body = client.get("/health").json()
    assert body["dependencies"]["store"] == "ready"


def test_store_status_defaults_without_state() -> None:
    assert store_status(FastAPI()) == STORE_NOT_CONFIGURED


def test_routes_use_lifespan_graph_when_present() -> None:
    seen: list[tuple[object, object]] = []

    class _StubGraph:
        def invoke(self, payload: object, config: object) -> object:
            seen.append((payload, config))
            return {"messages": [HumanMessage(content="stub-reply")]}

        def stream(self, payload: object, config: object) -> object:
            raise AssertionError("stream must not be called by the invoke route")

    app = create_app()
    app.state.agent_graph = _StubGraph()
    client = TestClient(app)
    response = client.post("/api/v1/chat", json={"message": "hi", "thread_id": "tid-stub"})
    assert response.status_code == 200
    assert response.json() == {"reply": "stub-reply", "thread_id": "tid-stub"}
    assert get_agent_graph(app) is app.state.agent_graph
    assert seen and seen[0][1] == {"configurable": {"thread_id": "tid-stub"}}


def test_routes_fall_back_to_stateless_graph() -> None:
    app = create_app()
    assert get_agent_graph(app) is graph
    client = TestClient(app)
    response = client.post("/api/v1/chat", json={"message": "hi", "thread_id": "tid-x"})
    assert response.json() == {"reply": "hi", "thread_id": "tid-x"}


def test_lifespan_empty_uri_stays_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _run() -> FastAPI:
        get_settings.cache_clear()
        monkeypatch.setenv("POSTGRES_URI", "")
        get_settings.cache_clear()
        try:
            app = FastAPI()
            async with database_lifespan(app):
                assert store_status(app) == STORE_NOT_CONFIGURED
                assert getattr(app.state, "agent_graph", None) is None
            return app
        finally:
            monkeypatch.undo()
            get_settings.cache_clear()

    asyncio.run(_run())


def test_lifespan_unreachable_db_degrades_to_stateless(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _run() -> None:
        monkeypatch.setenv("POSTGRES_URI", "postgresql://postgres:postgres@127.0.0.1:54329/pg")
        get_settings.cache_clear()
        try:
            app = FastAPI()
            async with database_lifespan(app):
                assert store_status(app) == STORE_UNAVAILABLE
                assert getattr(app.state, "agent_graph", None) is None
        finally:
            monkeypatch.undo()
            get_settings.cache_clear()

    asyncio.run(_run())
