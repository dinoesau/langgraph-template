"""Unit tests for the S5 GraphState extension.

No network and no containers: new keys are structural only.
"""

from langchain_core.messages import HumanMessage

from agent.graph import graph


def test_new_keys_absent_by_default() -> None:
    result = graph.invoke({"messages": [HumanMessage(content="hello")]})
    assert result.get("passages") == []
    assert result.get("triage_decision") is None
    assert result.get("sentiment") is None
    assert result.get("urgency") is None
    assert result.get("category") is None
    assert result.get("ticket_id") is None
    assert result.get("attempts") is None
    assert result.get("grade_feedback") is None


def test_docs_stays_recall_only() -> None:
    result = graph.invoke({"messages": [HumanMessage(content="hello")]})
    assert result.get("docs") is None
