"""Unit tests for S2b per-user recall plus cache plus background writes.

No network and no containers: vector reads use the in-memory fallback,
the cache uses the local TTL map, background writes never fail the turn.
"""

import asyncio
from collections.abc import Generator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage

from agent.graph import make_the_agent
from api.app import create_app
from core.cache import build_memory_key, clear_cache_singleton, get_cache
from core.config import get_settings
from services import memory
from services.memory import clear_memory_singleton


@pytest.fixture(name="isolated_memory")
def isolated_memory_fixture(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    """Point settings at ephemeral backends and drop every singleton."""
    monkeypatch.setenv("POSTGRES_URI", "")
    monkeypatch.setenv("VALKEY_HOST", "")
    monkeypatch.setenv("CACHE_TTL_SECONDS", "60")
    get_settings.cache_clear()
    clear_cache_singleton()
    clear_memory_singleton()
    yield
    get_settings.cache_clear()
    clear_cache_singleton()
    clear_memory_singleton()


def test_cache_key_scoped_per_user(isolated_memory: None) -> None:
    alice = build_memory_key("alice", "  Hello World ")
    bob = build_memory_key("bob", "hello world")
    again = build_memory_key("alice", "hello   world")
    assert alice != bob
    assert alice == again


def test_empty_user_or_query_returns_no_recall(isolated_memory: None) -> None:
    assert asyncio.run(memory.search("", "hello")) == ""
    assert asyncio.run(memory.search("alice", "   ")) == ""


def test_store_and_search_stays_per_user(isolated_memory: None) -> None:
    asyncio.run(memory.get_memory_store().add("alice", ["likes green tea"]))
    alice = asyncio.run(memory.search("alice", "green tea"))
    bob = asyncio.run(memory.search("bob", "green tea"))
    assert "green tea" in alice
    assert bob == ""


def test_repeated_recall_hits_cache(isolated_memory: None) -> None:
    asyncio.run(memory.get_memory_store().add("alice", ["prefers morning runs"]))
    first = asyncio.run(memory.search("alice", "morning runs"))
    assert first != ""
    clear_memory_singleton()
    second = asyncio.run(memory.search("alice", "morning runs"))
    assert second == first


def test_errors_are_never_cached(isolated_memory: None) -> None:
    class FailingStore:
        async def search(self, user_id: str, query: str, limit: int) -> list[str]:
            raise RuntimeError("vector down")

        async def add(self, user_id: str, texts: list[str]) -> None:
            raise RuntimeError("vector down")

    memory._memory_store = FailingStore()
    assert asyncio.run(memory.search("alice", "green tea")) == ""
    clear_memory_singleton()
    asyncio.run(memory.get_memory_store().add("alice", ["likes green tea"]))
    assert "green tea" in asyncio.run(memory.search("alice", "green tea"))


def test_store_failure_is_logged_and_ignored(isolated_memory: None) -> None:
    class FailingStore:
        async def search(self, user_id: str, query: str, limit: int) -> list[str]:
            raise RuntimeError("down")

        async def add(self, user_id: str, texts: list[str]) -> None:
            raise RuntimeError("down")

    memory._memory_store = FailingStore()
    asyncio.run(memory.store_turn("alice", [HumanMessage(content="hello")]))


def test_loop_recall_isolated_per_user(isolated_memory: None) -> None:
    async def _run() -> tuple[Any, Any]:
        await memory.get_memory_store().add("alice", ["alice likes green tea"])
        agent = make_the_agent(recall_hook=memory.search, store_hook=memory.schedule_store)
        alice = await agent.ainvoke(
            {"messages": [HumanMessage(content="green tea")]},
            config={"configurable": {"thread_id": "t1", "user_id": "alice"}},
        )
        bob = await agent.ainvoke(
            {"messages": [HumanMessage(content="green tea")]},
            config={"configurable": {"thread_id": "t2", "user_id": "bob"}},
        )
        return alice, bob

    alice_state, bob_state = asyncio.run(_run())
    assert alice_state.get("docs")
    assert not bob_state.get("docs")


def test_loop_without_user_id_skips_recall(isolated_memory: None) -> None:
    async def _run() -> Any:
        agent = make_the_agent(recall_hook=memory.search, store_hook=memory.schedule_store)
        return await agent.ainvoke(
            {"messages": [HumanMessage(content="hello")]},
            config={"configurable": {"thread_id": "t1"}},
        )

    result = asyncio.run(_run())
    assert not result.get("docs")


def test_http_accepts_transitional_user_header(isolated_memory: None) -> None:
    client = TestClient(create_app())
    response = client.post("/api/v1/chat", json={"message": "hello"}, headers={"X-User-ID": "a"})
    assert response.status_code == 200
    assert response.json()["reply"] == "Echo: hello"


def test_cache_falls_back_without_valkey(isolated_memory: None) -> None:
    async def _run() -> str | None:
        cache = get_cache()
        await cache.set("k", "v", 60)
        return await cache.get("k")

    assert asyncio.run(_run()) == "v"
