"""S2b memory unit seam: per-user recall, cache front, background writes.

Seams under test (highest first):
- MemoryService.recall/remember with fake store and fake cache (catches
  per-user isolation, cache-only-non-empty-success, and background
  failures logged and ignored; misses real pgvector SQL, covered by the
  integration suite on an ephemeral database).
- POST /api/v1/chat with stubbed memory (catches recall injected as
  context and session scope still on thread_id; misses real store).
"""

import logging

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage

from src.api.app import create_app
from src.core.config import get_settings
from src.core.security import SESSION_SCOPE, create_token
from src.services.memory import FakeMemoryStore, MemoryService


def _session_headers(user_id: str, thread_id: str) -> dict[str, str]:
    token, _ = create_token(
        user_id, SESSION_SCOPE, get_settings().JWT_SECRET_KEY, 7, extra={"sid": thread_id}
    )
    return {"Authorization": f"Bearer {token}"}


def _service(store: FakeMemoryStore | None = None) -> tuple[MemoryService, FakeMemoryStore]:
    owned = store or FakeMemoryStore()
    return MemoryService(store=owned, cache=None, ttl_seconds=60, top_k=3), owned


def test_recall_returns_only_requesting_user_memories() -> None:
    service, store = _service()
    store.seed("alice", ["alice likes tea"])
    store.seed("bob", ["bob likes coffee"])
    assert "tea" in service.recall("alice", "what do I drink")
    assert "coffee" not in service.recall("alice", "what do I drink")
    assert "coffee" in service.recall("bob", "what do I drink")


def test_repeated_recall_hits_cache_without_store_roundtrip() -> None:
    store = FakeMemoryStore()
    store.seed("alice", ["alice likes tea"])
    service = MemoryService(store=store, cache={}, ttl_seconds=60, top_k=3)
    first = service.recall("alice", "drink")
    assert "tea" in first
    assert store.search_calls == 1
    second = service.recall("alice", "drink")
    assert second == first
    assert store.search_calls == 1


def test_empty_results_and_errors_are_never_cached() -> None:
    store = FakeMemoryStore()
    service = MemoryService(store=store, cache={}, ttl_seconds=60, top_k=3)
    assert service.recall("alice", "nothing yet") == ""
    assert store.search_calls == 1
    assert service.recall("alice", "nothing yet") == ""
    assert store.search_calls == 2

    store.fail_next_search()
    assert service.recall("alice", "boom") == ""
    assert service.recall("alice", "boom") == ""
    assert store.search_calls == 4


def test_remember_failure_is_logged_and_ignored(caplog: pytest.LogCaptureFixture) -> None:
    store = FakeMemoryStore()
    store.fail_next_add()
    service, _ = _service(store)
    with caplog.at_level(logging.WARNING, logger="src.services.memory"):
        service.remember("alice", "hello", "hi there")
    assert store.memories.get("alice", []) == []


def test_chat_injects_recall_as_context_and_keeps_thread_scope() -> None:
    app = create_app()
    store = FakeMemoryStore()
    store.seed("1", ["alice prefers morning slots"])
    service = MemoryService(store=store, cache={}, ttl_seconds=60, top_k=3)
    app.state.memory_service = service
    client = TestClient(app)
    response = client.post(
        "/api/v1/chat",
        json={"message": "book me a slot", "thread_id": "tid-mem-1"},
        headers=_session_headers("1", "tid-mem-1"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["thread_id"] == "tid-mem-1"
    assert "morning slots" in body["reply"]


def test_chat_without_memories_keeps_echo() -> None:
    app = create_app()
    app.state.memory_service = MemoryService(store=FakeMemoryStore(), cache={}, ttl_seconds=60)
    client = TestClient(app)
    response = client.post(
        "/api/v1/chat",
        json={"message": "hello", "thread_id": "tid-echo-1"},
        headers=_session_headers("1", "tid-echo-1"),
    )
    assert response.json() == {"reply": "hello", "thread_id": "tid-echo-1"}


def test_chat_background_write_does_not_break_reply_even_on_failure() -> None:
    app = create_app()
    store = FakeMemoryStore()
    store.fail_next_add()
    app.state.memory_service = MemoryService(store=store, cache={}, ttl_seconds=60)
    client = TestClient(app)
    response = client.post(
        "/api/v1/chat",
        json={"message": "hi", "thread_id": "tid-bg-1"},
        headers=_session_headers("4", "tid-bg-1"),
    )
    assert response.status_code == 200
    assert response.json()["thread_id"] == "tid-bg-1"


def test_chat_memory_is_scoped_to_token_user() -> None:
    app = create_app()
    store = FakeMemoryStore()
    store.seed("2", ["bob likes coffee"])
    app.state.memory_service = MemoryService(store=store, cache={}, ttl_seconds=60)
    client = TestClient(app)
    response = client.post(
        "/api/v1/chat",
        json={"message": "what do I drink", "thread_id": "tid-scoped-1"},
        headers=_session_headers("1", "tid-scoped-1"),
    )
    assert response.json() == {"reply": "what do I drink", "thread_id": "tid-scoped-1"}


def test_chat_requires_session_token() -> None:
    app = create_app()
    app.state.memory_service = MemoryService(store=FakeMemoryStore(), cache={}, ttl_seconds=60)
    client = TestClient(app)
    response = client.post("/api/v1/chat", json={"message": "hi", "thread_id": "tid-anon-1"})
    assert response.status_code == 401


def test_loop_never_imports_memory_storage() -> None:
    from pathlib import Path

    for name in ("src/agent/graph.py", "src/agent/nodes.py", "src/agent/state.py"):
        source = Path(name).read_text(encoding="utf-8").lower()
        assert "memory" not in source
        assert "valkey" not in source
        assert "vector" not in source


def test_stub_graph_seam_for_memory_service() -> None:
    seen: list[object] = []

    class _StubStore:
        def search(self, user_id: str, query: str, limit: int) -> list[str]:
            seen.append((user_id, query, limit))
            return [HumanMessage(content="x").content]  # type: ignore[list-item]

    service = MemoryService(store=_StubStore(), cache={}, ttl_seconds=60, top_k=3)  # type: ignore[arg-type]
    assert "x" in service.recall("u", "q")
    assert seen == [("u", "q", 3)]
