"""Fakes for S5 unit suites: deterministic llm, seeded passages, fixed states.

No network and no containers. Upward stack PRs extend this module
additively and never rewrite its helpers.
"""

from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.store.memory import InMemoryStore

from core.memory import passage_namespace

POLICY_PASSAGES = [
    "Refunds are available within 30 days of purchase with a receipt.",
    "Digital bookings can be rescheduled up to 24 hours before the slot.",
]


class FakeLlm:
    """Deterministic stand-in for LlmService with the same call surface."""

    def __init__(self, replies: list[str] | None = None, failures: int = 0) -> None:
        self.replies = list(replies or [])
        self.failures = failures
        self.calls: list[list[BaseMessage]] = []

    def _next(self) -> AIMessage:
        if len(self.calls) <= self.failures:
            raise RuntimeError("fake llm failure")
        if self.replies:
            return AIMessage(content=self.replies.pop(0))
        return AIMessage(content="Fake reply")

    def call(
        self,
        messages: list[BaseMessage],
        tools: list[Any] | None = None,
        response_format: Any | None = None,
    ) -> AIMessage:
        """Sync entry; records messages and returns the next scripted reply."""
        self.calls.append(list(messages))
        return self._next()

    async def acall(
        self,
        messages: list[BaseMessage],
        tools: list[Any] | None = None,
        response_format: Any | None = None,
    ) -> AIMessage:
        """Async entry with the same semantics as the sync entry."""
        self.calls.append(list(messages))
        return self._next()


def make_store(seed: bool = True) -> InMemoryStore:
    """Return an in-memory store, optionally seeded with policy passages."""
    store = InMemoryStore()
    if seed:
        seed_passages(store, POLICY_PASSAGES)
    return store


def seed_passages(store: InMemoryStore, texts: list[str]) -> None:
    """Write policy chunks into the shared passage namespace."""
    for index, text in enumerate(texts):
        store.put(passage_namespace(), f"p{index}", {"text": text})


def fixed_state(text: str) -> dict[str, list[BaseMessage]]:
    """Return a minimal turn state with one human message."""
    return {"messages": [HumanMessage(content=text)]}
