"""S5a integration: real provider models prove grounded answers end to end.

Runs only when provider keys are present, never fails the default gate.
"""

import asyncio
import os
from typing import Any

import pytest
from langchain_core.messages import HumanMessage

from src.agent.rag import UNKNOWN_REPLY, make_rag_agent

needs_openai = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"), reason="OPENAI_API_KEY is not set"
)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


@needs_openai
def test_real_model_answers_refund_from_passages() -> None:
    agent = make_rag_agent()
    result = _run(
        agent.ainvoke(
            {"messages": [HumanMessage(content="what is your refund policy")]},
            config={"configurable": {"thread_id": "integration-rag-1"}},
        )
    )
    assert result["docs"] != []
    assert "30 days" in str(result["messages"][-1].content)


@needs_openai
def test_real_model_says_unknown_when_unsupported() -> None:
    agent = make_rag_agent()
    result = _run(
        agent.ainvoke(
            {"messages": [HumanMessage(content="qzxwvv off-policy jdskflajsd")]},
            config={"configurable": {"thread_id": "integration-rag-2"}},
        )
    )
    assert str(result["messages"][-1].content) == UNKNOWN_REPLY
