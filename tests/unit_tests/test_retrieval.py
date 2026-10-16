"""S5a grounded retrieval seam: top-k for last user message only.

Seams under test (highest first):
- retrieve with stub store (catches last-user-message-only query and top-k
  limit; misses real embedding ranking, covered by the integration suite).
- generate with stubbed llm_service.call (catches docs cited in the grounded
  prompt plus the unknown reply on empty docs; misses real model wording,
  covered by the integration suite on a real provider).
"""

from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from src.agent.rag import UNKNOWN_REPLY, generate, retrieve
from src.agent.state import GraphState
from src.services.llm.service import llm_service
from src.services.retrieval import RetrieverStore


class _StubStore:
    """Test double recording queries, returning fixed chunks up to limit."""

    def __init__(self, docs: list[str]) -> None:
        self._docs = list(docs)
        self.queries: list[str] = []

    def search(self, query: str, limit: int) -> list[str]:
        self.queries.append(query)
        return list(self._docs[:limit])


def _run(coro: object) -> object:
    import asyncio

    assert asyncio.iscoroutine(coro)
    return asyncio.run(coro)


def test_retrieve_uses_only_last_user_message() -> None:
    store: RetrieverStore = _StubStore(["refund within 30 days", "extra chunk"])
    state = GraphState(
        messages=[
            HumanMessage(content="what is your refund policy"),
            AIMessage(content="ask me more"),
            HumanMessage(content="shipping costs please"),
        ]
    )
    out = _run(retrieve(state, store=store, top_k=3))
    assert isinstance(out, dict)
    assert out["docs"] == ["refund within 30 days", "extra chunk"]
    assert store.queries == ["shipping costs please"]  # type: ignore[attr-defined]


def test_retrieve_respects_top_k() -> None:
    store: RetrieverStore = _StubStore([f"chunk {index}" for index in range(5)])
    state = GraphState(messages=[HumanMessage(content="refund")])
    out = _run(retrieve(state, store=store, top_k=2))
    assert isinstance(out, dict)
    assert out["docs"] == ["chunk 0", "chunk 1"]


def test_generate_cites_retrieved_facts(monkeypatch: Any) -> None:
    seen: list[list[BaseMessage]] = []

    async def _fake_call(messages: list[BaseMessage], **kwargs: Any) -> AIMessage:
        seen.append(list(messages))
        return AIMessage(content="Full refunds within 30 days [1].")

    monkeypatch.setattr(llm_service, "call", _fake_call)
    state = GraphState(
        messages=[HumanMessage(content="what is your refund policy")],
        docs=["Refund policy: full refunds within 30 days of purchase."],
    )
    out = _run(generate(state))
    assert isinstance(out, dict)
    reply = str(out["messages"][0].content)
    assert "30 days" in reply
    prompt = " ".join(str(m.content) for m in seen[0])
    assert "full refunds within 30 days" in prompt


def test_generate_unknown_without_docs(monkeypatch: Any) -> None:
    async def _boom(messages: object, **kwargs: object) -> AIMessage:
        raise AssertionError("llm_service.call must not run with empty docs")

    monkeypatch.setattr(llm_service, "call", _boom)
    state = GraphState(messages=[HumanMessage(content="off-policy question")], docs=[])
    out = _run(generate(state))
    assert isinstance(out, dict)
    assert str(out["messages"][0].content) == UNKNOWN_REPLY


def test_memory_never_overrides_grounding(monkeypatch: Any) -> None:
    seen: list[list[BaseMessage]] = []

    async def _fake_call(messages: list[BaseMessage], **kwargs: Any) -> AIMessage:
        seen.append(list(messages))
        return AIMessage(content="Full refunds within 30 days [1].")

    monkeypatch.setattr(llm_service, "call", _fake_call)
    state = GraphState(
        messages=[HumanMessage(content="how long do I have for a refund")],
        docs=["Refund policy: full refunds within 30 days of purchase."],
    )
    out = _run(generate(state, memory_context="The user believes refunds last 90 days."))
    assert isinstance(out, dict)
    reply = str(out["messages"][0].content)
    assert "30 days" in reply
    assert "90 days" not in reply
    prompt = " ".join(str(m.content) for m in seen[0])
    assert "NEVER overrides" in prompt
    assert "90 days" in prompt


def test_rag_graph_runs_retrieve_then_generate(monkeypatch: Any) -> None:
    from src.agent.rag import make_rag_agent

    async def _fake_call(messages: list[BaseMessage], **kwargs: Any) -> AIMessage:
        return AIMessage(content="Standard shipping takes 5 to 7 days [1].")

    monkeypatch.setattr(llm_service, "call", _fake_call)
    agent = make_rag_agent()
    result = _run(agent.ainvoke({"messages": [HumanMessage(content="how fast is shipping")]}))
    assert isinstance(result, dict)
    assert result["docs"] != []
    assert "5 to 7 days" in str(result["messages"][-1].content)
