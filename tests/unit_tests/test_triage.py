"""S5c seam: ticket fan-out to parallel workers merged into one triage card."""

import asyncio
from typing import Any

from langchain_core.messages import AIMessage

from src.services.llm.service import llm_service


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_fan_out_produces_three_workers_with_same_text() -> None:
    from src.agent.triage import fan_out

    state = {"ticket_id": "t-1", "ticket_text": "refund please", "results": [], "card": {}}
    sends = fan_out(state)  # type: ignore[arg-type]
    assert len(sends) == 3
    assert {s.node for s in sends} == {"sentiment", "urgency", "category"}
    texts = {s.arg["ticket_text"] for s in sends}
    assert texts == {"refund please"}


def test_workers_read_same_text_and_stay_deterministic(monkeypatch: Any) -> None:
    from src.agent.triage import category_worker, sentiment_worker

    seen: list[str] = []

    async def _fake_call(messages: Any, **kwargs: Any) -> AIMessage:
        seen.append(str(messages[-1].content))
        if "sentiment" in str(messages[-1].content).lower():
            return AIMessage(content="negative")
        return AIMessage(content="refund")

    monkeypatch.setattr(llm_service, "call", _fake_call)
    state = {"ticket_id": "t-1", "ticket_text": "same text", "results": [], "card": {}}
    first = _run(sentiment_worker(state))  # type: ignore[arg-type]
    second = _run(sentiment_worker(state))  # type: ignore[arg-type]
    assert first == second
    assert first == {"results": [{"sentiment": "negative"}]}
    cat = _run(category_worker(state))  # type: ignore[arg-type]
    assert cat == {"results": [{"category": "refund"}]}
    assert all("same text" in text for text in seen)


def test_parallel_writes_do_not_clobber(monkeypatch: Any) -> None:
    from src.agent.triage import make_triage_graph

    async def _fake_call(messages: Any, **kwargs: Any) -> AIMessage:
        text = str(messages[-1].content).lower()
        if "sentiment" in text:
            return AIMessage(content="negative")
        if "urgency" in text:
            return AIMessage(content="high")
        return AIMessage(content="refund")

    monkeypatch.setattr(llm_service, "call", _fake_call)
    graph = make_triage_graph()
    result = _run(graph.ainvoke({"ticket_id": "t-42", "ticket_text": "urgent refund"}))
    assert len(result["results"]) == 3
    merged: dict[str, str] = {}
    for item in result["results"]:
        for key in item:
            assert key not in merged
            merged[key] = item[key]
    assert set(merged) == {"sentiment", "urgency", "category"}


def test_aggregator_merges_three_fields_plus_ticket_id() -> None:
    from src.agent.triage import aggregator

    state = {
        "ticket_id": "t-7",
        "ticket_text": "x",
        "results": [{"sentiment": "negative"}, {"urgency": "high"}, {"category": "refund"}],
        "card": {},
    }
    out = _run(aggregator(state))  # type: ignore[arg-type]
    assert out["card"] == {
        "ticket_id": "t-7",
        "sentiment": "negative",
        "urgency": "high",
        "category": "refund",
    }


def test_triage_graph_card_shape_end_to_end(monkeypatch: Any) -> None:
    from src.agent.triage import make_triage_graph

    async def _fake_call(messages: Any, **kwargs: Any) -> AIMessage:
        text = str(messages[-1].content).lower()
        if "sentiment" in text:
            return AIMessage(content="negative")
        if "urgency" in text:
            return AIMessage(content="high")
        return AIMessage(content="refund")

    monkeypatch.setattr(llm_service, "call", _fake_call)
    graph = make_triage_graph()
    result = _run(graph.ainvoke({"ticket_id": "t-9", "ticket_text": "help"}))
    assert result["card"] == {
        "ticket_id": "t-9",
        "sentiment": "negative",
        "urgency": "high",
        "category": "refund",
    }
