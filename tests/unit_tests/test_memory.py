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
from langchain_core.runnables import RunnableConfig

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


def test_recall_parity_across_invoke_ainvoke_astream(isolated_memory: None) -> None:
    async def _run() -> tuple[Any, Any, list[Any]]:
        await memory.get_memory_store().add("alice", ["alice likes green tea"])
        agent = make_the_agent(recall_hook=memory.search, store_hook=memory.schedule_store)
        config: RunnableConfig = {"configurable": {"thread_id": "parity", "user_id": "alice"}}

        def _invoke_sync() -> Any:
            return agent.invoke({"messages": [HumanMessage(content="green tea")]}, config)

        sync_result = await asyncio.to_thread(_invoke_sync)
        async_result: Any = await agent.ainvoke(
            {"messages": [HumanMessage(content="green tea")]}, config=config
        )
        chunks = [
            chunk
            async for chunk in agent.astream(
                {"messages": [HumanMessage(content="green tea")]},
                config=config,
                stream_mode="updates",
            )
        ]
        await memory.drain_background_writes()
        return sync_result, async_result, chunks

    sync_result, async_result, chunks = asyncio.run(_run())
    assert sync_result.get("docs")
    assert async_result.get("docs")
    assert sync_result["messages"][-1].content == async_result["messages"][-1].content
    assert any("docs" in chunk.get("echo", {}) for chunk in chunks)


def test_ephemeral_lifespan_wires_recall(isolated_memory: None) -> None:
    async def _seed() -> None:
        await memory.get_memory_store().add("alice", ["alice likes green tea"])

    asyncio.run(_seed())
    app = create_app()
    with TestClient(app) as client:
        assert app.state.agent_graph is not None
        response = client.post(
            "/api/v1/chat",
            json={"message": "green tea", "thread_id": "lifespan-t1"},
            headers={"X-User-ID": "alice"},
        )
    assert response.status_code == 200
    assert response.json()["reply"] == "Echo: green tea"

    async def _recall_ran() -> str | None:
        from core.cache import build_memory_key as key
        from core.cache import get_cache as cache

        return await cache().get(key("alice", "green tea"))

    assert asyncio.run(_recall_ran())


def test_http_recall_stays_per_user(isolated_memory: None) -> None:
    app = create_app()
    with TestClient(app) as client:
        client.post(
            "/api/v1/chat",
            json={"message": "alice customtea", "thread_id": "http-alice"},
            headers={"X-User-ID": "alice"},
        )
        client.post(
            "/api/v1/chat",
            json={"message": "bob customtea", "thread_id": "http-bob"},
            headers={"X-User-ID": "bob"},
        )

    async def _stored() -> tuple[list[str], list[str]]:
        store = memory.get_memory_store()
        return (
            await store.search("alice", "customtea", 5),
            await store.search("bob", "customtea", 5),
        )

    alice_rows, bob_rows = asyncio.run(_stored())
    assert any("alice customtea" in row for row in alice_rows)
    assert all("alice customtea" not in row for row in bob_rows)
    assert any("bob customtea" in row for row in bob_rows)
    assert all("bob customtea" not in row for row in alice_rows)


def test_production_ignores_transitional_header(
    isolated_memory: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    get_settings.cache_clear()
    clear_cache_singleton()
    clear_memory_singleton()
    app = create_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/chat",
            json={"message": "green tea", "thread_id": "prod-t1"},
            headers={"X-User-ID": "alice"},
        )
    assert response.status_code == 200

    async def _stored() -> list[str]:
        return await memory.get_memory_store().search("alice", "green tea", 5)

    assert asyncio.run(_stored()) == []


def test_set_warms_fallback_when_primary_succeeds(isolated_memory: None) -> None:
    from core.cache import InMemoryTTLCache, RecallCache

    class GoodPrimary:
        def __init__(self) -> None:
            self.saved: dict[str, str] = {}

        async def get(self, key: str) -> str | None:
            return self.saved.get(key)

        async def set(self, key: str, value: str, ttl: int) -> None:
            self.saved[key] = value

    async def _run() -> str | None:
        fallback = InMemoryTTLCache()
        front = RecallCache(primary=GoodPrimary(), fallback=fallback)
        await front.set("k", "v", 60)
        return await fallback.get("k")

    assert asyncio.run(_run()) == "v"


def test_failing_primary_falls_back(isolated_memory: None) -> None:
    from core.cache import InMemoryTTLCache, RecallCache

    class BadPrimary:
        async def get(self, key: str) -> str | None:
            raise RuntimeError("valkey down")

        async def set(self, key: str, value: str, ttl: int) -> None:
            raise RuntimeError("valkey down")

    async def _run() -> str | None:
        front = RecallCache(primary=BadPrimary(), fallback=InMemoryTTLCache())
        await front.set("k", "v", 60)
        return await front.get("k")

    assert asyncio.run(_run()) == "v"


def test_concurrent_recall_stays_isolated(isolated_memory: None) -> None:
    async def _run() -> tuple[str, str, str, str]:
        await memory.get_memory_store().add("alice", ["alice likes green tea"])
        await memory.get_memory_store().add("bob", ["bob likes black coffee"])
        return await asyncio.gather(
            memory.search("alice", "green tea"),
            memory.search("bob", "black coffee"),
            memory.search("alice", "green tea"),
            memory.search("mallory", "green tea"),
        )

    alice, bob, alice_again, mallory = asyncio.run(_run())
    assert "green tea" in alice
    assert "black coffee" in bob
    assert alice == alice_again
    assert mallory == ""


def test_background_writes_drain_on_demand(isolated_memory: None) -> None:
    from langchain_core.messages import AIMessage

    async def _run() -> list[str]:
        memory.schedule_store("alice", [HumanMessage(content="green tea")])
        memory.schedule_store(
            "alice", [HumanMessage(content="green tea"), AIMessage(content="noted")]
        )
        await memory.drain_background_writes()
        return await memory.get_memory_store().search("alice", "green tea", 5)

    assert asyncio.run(_run())


def test_sync_path_persists_without_running_loop(isolated_memory: None) -> None:
    agent = make_the_agent(recall_hook=memory.search, store_hook=memory.schedule_store)
    agent.invoke(
        {"messages": [HumanMessage(content="green tea")]},
        config={"configurable": {"thread_id": "sync-t1", "user_id": "alice"}},
    )
    assert asyncio.run(memory.search("alice", "green tea")) != ""


def test_escape_like_neutralizes_wildcards(isolated_memory: None) -> None:
    assert memory.escape_like("100%_x\\y") == "100\\%\\_x\\\\y"
    asyncio.run(memory.get_memory_store().add("alice", ["likes green tea"]))
    assert asyncio.run(memory.search("alice", "%")) == ""


def test_memory_settings_load_from_env(
    isolated_memory: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LONG_TERM_MEMORY_COLLECTION_NAME", "custom_mem")
    monkeypatch.setenv("MEMORY_DISTANCE_MAX", "1.5")
    monkeypatch.setenv("VALKEY_HOST", "")
    get_settings.cache_clear()
    clear_cache_singleton()
    clear_memory_singleton()
    settings = get_settings()
    assert settings.long_term_memory_collection_name == "custom_mem"
    assert settings.memory_distance_max == 1.5
