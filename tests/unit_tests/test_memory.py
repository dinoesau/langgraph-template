"""Unit tests for S2b per-user recall plus cache plus background writes.

No network and no containers: the store is InMemoryStore, the cache is
the local TTL map, background writes run as Starlette background tasks.
"""

import asyncio
from collections.abc import Generator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore

from agent.graph import make_the_agent
from api.app import create_app
from core.cache import RecallCache, build_cache, build_memory_key
from core.config import get_settings
from services.memory import extract_memories, format_recall, memory_namespace, persist_turn


@pytest.fixture(name="clean_env")
def clean_env_fixture(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    """Point settings at ephemeral backends for every test."""
    monkeypatch.setenv("POSTGRES_URI", "")
    monkeypatch.setenv("VALKEY_HOST", "")
    monkeypatch.setenv("APP_ENV", "development")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def make_parts() -> tuple[InMemoryStore, RecallCache]:
    """Return an isolated store plus cache front for one test."""
    return InMemoryStore(), build_cache("")


def make_agent(store: BaseStore, cache: RecallCache | None = None, ttl: int = 60) -> Any:
    """Compile the loop with recall wired like the lifespan does."""
    return make_the_agent(store=store, cache=cache, cache_ttl_seconds=ttl)


def seed(store: BaseStore, user_id: str, text: str) -> None:
    async def _run() -> None:
        await store.aput(memory_namespace(user_id), text, {"text": text})

    asyncio.run(_run())


def test_cache_key_scoped_per_user(clean_env: None) -> None:
    alice = build_memory_key("alice", "  Hello World ")
    bob = build_memory_key("bob", "hello world")
    again = build_memory_key("alice", "hello   world")
    assert alice != bob
    assert alice == again


def test_namespace_scopes_per_user(clean_env: None) -> None:
    assert memory_namespace("alice") == ("memories", "alice")
    assert memory_namespace("alice") != memory_namespace("bob")


def test_extract_memories_keeps_last_texts(clean_env: None) -> None:
    messages = [
        HumanMessage(content="   "),
        HumanMessage(content="likes green tea"),
        AIMessage(content="Echo: likes green tea"),
    ]
    assert extract_memories(messages) == ["likes green tea", "likes green tea"]
    assert extract_memories([HumanMessage(content="   ")]) == []
    assert format_recall([]) == ""
    assert format_recall(["  ", "kept"]) == "- kept"


def test_loop_recall_isolated_per_user(clean_env: None) -> None:
    async def _run() -> tuple[Any, Any]:
        store, cache = make_parts()
        await store.aput(memory_namespace("alice"), "m1", {"text": "alice likes green tea"})
        await store.aput(memory_namespace("bob"), "m2", {"text": "bob likes black coffee"})
        agent = make_agent(store, cache)
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
    assert "green tea" in alice_state["docs"][0]
    assert "green tea" not in bob_state["docs"][0]
    assert "black coffee" not in alice_state["docs"][0]


def test_loop_without_user_id_skips_recall(clean_env: None) -> None:
    async def _run() -> Any:
        store, cache = make_parts()
        await store.aput(memory_namespace("alice"), "m1", {"text": "alice likes green tea"})
        agent = make_agent(store, cache)
        return await agent.ainvoke(
            {"messages": [HumanMessage(content="green tea")]},
            config={"configurable": {"thread_id": "t1"}},
        )

    assert asyncio.run(_run()).get("docs") is None


def test_bare_graph_stays_echo_only(clean_env: None) -> None:
    from agent.graph import graph

    result = graph.invoke(
        {"messages": [HumanMessage(content="green tea")]},
        config={"configurable": {"thread_id": "t1", "user_id": "alice"}},
    )
    assert result["messages"][-1].content == "Echo: green tea"
    assert result.get("docs") is None


def test_recall_parity_across_invoke_ainvoke_astream(clean_env: None) -> None:
    async def _run() -> tuple[Any, Any, list[Any]]:
        store, cache = make_parts()
        await store.aput(memory_namespace("alice"), "m1", {"text": "alice likes green tea"})
        agent = make_agent(store, cache)
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
        return sync_result, async_result, chunks

    sync_result, async_result, chunks = asyncio.run(_run())
    assert "green tea" in sync_result["docs"][0]
    assert "green tea" in async_result["docs"][0]
    assert sync_result["messages"][-1].content == async_result["messages"][-1].content
    assert any("docs" in chunk.get("echo", {}) for chunk in chunks)


def test_repeated_recall_hits_cache(clean_env: None) -> None:
    async def _run() -> tuple[str, str]:
        store, cache = make_parts()
        await store.aput(memory_namespace("alice"), "m1", {"text": "alice likes green tea"})
        agent = make_agent(store, cache)
        config: RunnableConfig = {"configurable": {"thread_id": "t1", "user_id": "alice"}}
        first: Any = await agent.ainvoke(
            {"messages": [HumanMessage(content="green tea")]}, config=config
        )
        agent_nostore = make_agent(InMemoryStore(), cache)
        second: Any = await agent_nostore.ainvoke(
            {"messages": [HumanMessage(content="green tea")]}, config=config
        )
        return first["docs"][0], second["docs"][0]

    first, second = asyncio.run(_run())
    assert first != ""
    assert second == first


def test_errors_are_never_cached(clean_env: None) -> None:
    class FailingStore(InMemoryStore):
        async def abatch(self, ops: Any) -> Any:
            raise RuntimeError("vector down")

    async def _run() -> tuple[Any, Any]:
        cache = build_cache("")
        agent_down = make_agent(FailingStore(), cache)
        config: RunnableConfig = {"configurable": {"thread_id": "t1", "user_id": "alice"}}
        down: Any = await agent_down.ainvoke(
            {"messages": [HumanMessage(content="green tea")]}, config=config
        )
        store, _ = make_parts()
        await store.aput(memory_namespace("alice"), "m1", {"text": "alice likes green tea"})
        agent_up = make_agent(store, cache)
        up: Any = await agent_up.ainvoke(
            {"messages": [HumanMessage(content="green tea")]}, config=config
        )
        return down, up

    down, up = asyncio.run(_run())
    assert down.get("docs") is None
    assert "green tea" in up["docs"][0]


def test_persist_turn_stores_and_ignores_failures(clean_env: None) -> None:
    async def _run() -> list[Any]:
        store, _ = make_parts()
        await persist_turn(store, "alice", [HumanMessage(content="green tea")])
        await persist_turn(store, "  ", [HumanMessage(content="green tea")])
        return await store.asearch(memory_namespace("alice"), query="green tea", limit=5)

    assert asyncio.run(_run())

    class FailingStore(InMemoryStore):
        async def abatch(self, ops: Any) -> Any:
            raise RuntimeError("down")

    asyncio.run(persist_turn(FailingStore(), "alice", [HumanMessage(content="hi")]))


def test_http_recall_stays_per_user(clean_env: None) -> None:
    app = create_app()
    with TestClient(app) as client:
        assert app.state.agent_store is not None
        first = client.post(
            "/api/v1/chat",
            json={"message": "alice customtea", "thread_id": "http-alice"},
            headers={"X-User-ID": "alice"},
        )
        second = client.post(
            "/api/v1/chat",
            json={"message": "bob customtea", "thread_id": "http-bob"},
            headers={"X-User-ID": "bob"},
        )
    assert first.status_code == 200
    assert second.status_code == 200

    async def _stored() -> tuple[list[Any], list[Any]]:
        store: BaseStore = app.state.agent_store
        return (
            await store.asearch(memory_namespace("alice"), query="customtea", limit=5),
            await store.asearch(memory_namespace("bob"), query="customtea", limit=5),
        )

    alice_rows, bob_rows = asyncio.run(_stored())
    assert any("alice customtea" in str(row.value) for row in alice_rows)
    assert all("alice customtea" not in str(row.value) for row in bob_rows)
    assert any("bob customtea" in str(row.value) for row in bob_rows)


def test_production_ignores_transitional_header(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    get_settings.cache_clear()
    app = create_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/chat",
            json={"message": "green tea", "thread_id": "prod-t1"},
            headers={"X-User-ID": "alice"},
        )
    assert response.status_code == 200

    async def _stored() -> list[Any]:
        rows = await app.state.agent_store.asearch(
            memory_namespace("alice"), query="green tea", limit=5
        )
        return list(rows)

    assert asyncio.run(_stored()) == []


def test_set_warms_fallback_when_primary_succeeds(clean_env: None) -> None:
    from core.cache import InMemoryTTLCache, RecallCache

    class GoodPrimary:
        def __init__(self) -> None:
            self.saved: dict[str, str] = {}

        def get_sync(self, key: str) -> str | None:
            return self.saved.get(key)

        def set_sync(self, key: str, value: str, ttl: int) -> None:
            self.saved[key] = value

        async def get(self, key: str) -> str | None:
            return self.saved.get(key)

        async def set(self, key: str, value: str, ttl: int) -> None:
            self.saved[key] = value

    async def _run() -> str | None:
        fallback = InMemoryTTLCache()
        front = RecallCache(primary=GoodPrimary(), fallback=fallback)
        await front.set("k", "v", 60)
        front.set_sync("k2", "v2", 60)
        assert await fallback.get("k") == "v"
        return fallback.get_sync("k2")

    assert asyncio.run(_run()) == "v2"


def test_failing_primary_falls_back(clean_env: None) -> None:
    from core.cache import InMemoryTTLCache, RecallCache

    class BadPrimary:
        def get_sync(self, key: str) -> str | None:
            raise RuntimeError("valkey down")

        def set_sync(self, key: str, value: str, ttl: int) -> None:
            raise RuntimeError("valkey down")

        async def get(self, key: str) -> str | None:
            raise RuntimeError("valkey down")

        async def set(self, key: str, value: str, ttl: int) -> None:
            raise RuntimeError("valkey down")

    async def _run() -> tuple[str | None, str | None]:
        front = RecallCache(primary=BadPrimary(), fallback=InMemoryTTLCache())
        await front.set("k", "v", 60)
        front.set_sync("k2", "v2", 60)
        return await front.get("k"), front.get_sync("k2")

    assert asyncio.run(_run()) == ("v", "v2")


def test_concurrent_recall_stays_isolated(clean_env: None) -> None:
    async def _run() -> tuple[list[Any], list[Any]]:
        store, _ = make_parts()
        await asyncio.gather(
            persist_turn(store, "alice", [HumanMessage(content="alice likes green tea")]),
            persist_turn(store, "bob", [HumanMessage(content="bob likes black coffee")]),
        )
        return await asyncio.gather(
            store.asearch(memory_namespace("alice"), query="green tea", limit=5),
            store.asearch(memory_namespace("bob"), query="black coffee", limit=5),
        )

    alice_rows, bob_rows = asyncio.run(_run())
    assert any("green tea" in str(row.value) for row in alice_rows)
    assert all("green tea" not in str(row.value) for row in bob_rows)


def test_memory_settings_load_from_env(clean_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CACHE_TTL_SECONDS", "120")
    monkeypatch.setenv("CACHE_MAX_ENTRIES", "500")
    get_settings.cache_clear()
    assert get_settings().cache_ttl_seconds == 120
    assert get_settings().cache_max_entries == 500


def test_cache_evicts_oldest_beyond_max(clean_env: None) -> None:
    async def _run() -> tuple[str | None, str | None, int]:
        cache = build_cache("", max_entries=2)
        await cache.set("k1", "v1", 60)
        await cache.set("k2", "v2", 60)
        await cache.set("k3", "v3", 60)
        return await cache.get("k1"), await cache.get("k3"), len(cache._fallback)

    evicted, kept, size = asyncio.run(_run())
    assert evicted is None
    assert kept == "v3"
    assert size == 2


def test_repeat_turns_do_not_duplicate_rows(clean_env: None) -> None:
    async def _run() -> list[Any]:
        store, _ = make_parts()
        turn = [
            HumanMessage(content="likes green tea"),
            AIMessage(content="Echo: likes green tea"),
        ]
        await persist_turn(store, "alice", turn)
        await persist_turn(store, "alice", turn)
        return list(await store.asearch(memory_namespace("alice"), query="green tea", limit=10))

    assert len(asyncio.run(_run())) == 1


def test_cache_close_is_safe_without_server(clean_env: None) -> None:
    async def _run() -> None:
        cache = build_cache("")
        await cache.set("k", "v", 60)
        await cache.aclose()
        await cache.aclose()
        cache.close_sync()

    asyncio.run(_run())


def test_overlong_user_id_degrades_closed(clean_env: None) -> None:
    from starlette.requests import Request

    from api.deps import get_request_user_id

    def _request(uid: str) -> Request:
        return Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/v1/chat",
                "headers": [(b"x-user-id", uid.encode())],
            }
        )

    long_a = "a" * 128 + "1"
    long_b = "a" * 128 + "2"
    assert get_request_user_id(_request(long_a)) == ""
    assert get_request_user_id(_request(long_b)) == ""
    assert get_request_user_id(_request("alice")) == "alice"


def test_lifespan_closes_cache_on_shutdown(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core.cache import InMemoryTTLCache, RecallCache

    closed: list[str] = []

    class RecordingPrimary:
        def get_sync(self, key: str) -> str | None:
            return None

        def set_sync(self, key: str, value: str, ttl: int) -> None:
            return None

        async def get(self, key: str) -> str | None:
            return None

        async def set(self, key: str, value: str, ttl: int) -> None:
            return None

        def close_sync(self) -> None:
            closed.append("sync")

        async def aclose(self) -> None:
            closed.append("async")

    from services import database

    def _recording(host: str, max_entries: int = 1000) -> RecallCache:
        return RecallCache(primary=RecordingPrimary(), fallback=InMemoryTTLCache())

    monkeypatch.setattr(database, "build_cache", _recording)
    with TestClient(create_app()):
        pass
    assert "async" in closed
    assert "sync" in closed
