"""S3 integration: real provider models prove the loop end to end.

Runs only when provider keys are present, never fails the default gate.
"""

import asyncio
import os
from typing import Any

import pytest
from langchain_core.messages import HumanMessage

from src.agent.graph import make_the_agent
from src.services.llm.registry import (
    FALLBACK_ANTHROPIC,
    FALLBACK_GOOGLE,
    MODEL_ORDER,
    ordered_names,
)
from src.services.llm.service import LLMService

needs_openai = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"), reason="OPENAI_API_KEY is not set"
)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_registry_order_matches_fallback_contract() -> None:
    assert list(MODEL_ORDER) == ["default-openai", FALLBACK_ANTHROPIC, FALLBACK_GOOGLE]
    assert ordered_names(None, list(MODEL_ORDER))[0] in MODEL_ORDER


@needs_openai
def test_real_model_answers_without_tools() -> None:
    service = LLMService()
    result = _run(service.call([HumanMessage(content="Reply with exactly: ok")]))
    assert "ok" in str(result.content).lower()


@needs_openai
def test_real_model_loop_books_a_slot() -> None:
    agent = make_the_agent()
    result = _run(
        agent.ainvoke(
            {"messages": [HumanMessage(content="Is anything free on 2026-11-01?")]},
            config={"configurable": {"thread_id": "integration-s3"}},
        )
    )
    assert "2026-11-01" in str(result["messages"][-1].content)
