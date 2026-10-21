"""One model entry: retries per model, circular fallback, total budget.

Manual loop instead of ``with_fallbacks`` so start offset plus per-model
attempt counts stay explicit and testable. Tools rebind on every model
switch inside this module, so nodes never handle provider differences.
Structured output only applies as a terminal chain, never inside the tool
loop, because function-calling mode suppresses ``tool_calls``.
"""

import asyncio
import logging
import time
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.runnables import Runnable
from pydantic import BaseModel

from services.llm.registry import build_registry

logger = logging.getLogger("llm")


def is_retryable(error: Exception) -> bool:
    """Classify provider failures; auth and validation fail fast."""
    name = type(error).__name__.lower()
    text = f"{name} {error}".lower()
    non_retryable_markers = (
        "auth",
        "api key",
        "permission",
        "forbidden",
        "unauthorized",
        "invalid",
        "validation",
        "bad request",
        "not found",
    )
    if any(marker in text for marker in non_retryable_markers):
        return False
    if "401" in text or "403" in text or "400" in text or "404" in text:
        return False
    retryable_markers = (
        "rate",
        "429",
        "timeout",
        "timed out",
        "connection",
        "overloaded",
        "temporarily",
        "500",
        "502",
        "503",
        "504",
    )
    if any(marker in text for marker in retryable_markers):
        return True
    if isinstance(error, TimeoutError | ConnectionError):
        return True
    return True


def is_non_retryable(error: Exception) -> bool:
    """Mirror of retryable check for explicit fail-fast paths."""
    return not is_retryable(error)


class LlmService:
    """Call entry owned by the service side; loop passes messages plus tools."""

    def __init__(
        self,
        models: list[tuple[str, Any]],
        *,
        start: str = "",
        max_retries: int = 3,
        total_timeout_seconds: int = 60,
        backoff_min_seconds: float = 2.0,
        backoff_max_seconds: float = 10.0,
    ) -> None:
        if not models:
            raise ValueError("llm registry must hold at least one model")
        self._models = list(models)
        self._start = start
        self._max_retries = max(1, max_retries)
        self._total_timeout = max(1, total_timeout_seconds)
        self._backoff_min = float(backoff_min_seconds)
        self._backoff_max = float(backoff_max_seconds)

    @classmethod
    def from_settings(cls, settings: Any) -> "LlmService":
        """Build from typed settings, skipping providers without keys."""
        from core.config import Settings

        typed = settings if isinstance(settings, Settings) else Settings()
        registry = build_registry(typed)
        if not registry:
            raise ValueError("no llm provider key is set")
        built: list[tuple[str, BaseChatModel]] = [(entry.name, entry.build()) for entry in registry]
        return cls(
            built,
            start=typed.default_llm_model,
            max_retries=typed.max_llm_call_retries,
            total_timeout_seconds=typed.llm_total_timeout_seconds,
        )

    def _ordered(self) -> list[tuple[str, Any]]:
        names = [name for name, _ in self._models]
        if self._start and self._start in names:
            index = names.index(self._start)
            return self._models[index:] + self._models[:index]
        return list(self._models)

    def _backoff(self, attempt: int) -> float:
        return float(min(self._backoff_max, self._backoff_min * (2**attempt)))

    def call(
        self,
        messages: list[BaseMessage],
        tools: list[Any] | None = None,
        response_format: type[BaseModel] | None = None,
    ) -> Any:
        """Sync entry; retries one model then walks the registry once."""
        deadline = time.monotonic() + self._total_timeout
        last_error: Exception | None = None
        for name, raw_model in self._ordered():
            model = self._bind(raw_model, tools, response_format)
            for attempt in range(self._max_retries):
                if time.monotonic() >= deadline:
                    break
                try:
                    return model.invoke(messages)
                except Exception as exc:
                    last_error = exc
                    if is_non_retryable(exc):
                        raise
                    if attempt < self._max_retries - 1 and time.monotonic() < deadline:
                        delay = self._backoff(attempt)
                        if time.monotonic() + delay > deadline:
                            break
                        logger.warning("llm retry model=%s attempt=%d", name, attempt + 1)
                        time.sleep(delay)
            if time.monotonic() >= deadline:
                break
            logger.warning("llm fallback from model=%s", name)
        raise RuntimeError(f"MODEL_UNAVAILABLE: registry exhausted: {last_error}")

    async def acall(
        self,
        messages: list[BaseMessage],
        tools: list[Any] | None = None,
        response_format: type[BaseModel] | None = None,
    ) -> Any:
        """Async entry; same semantics as the sync entry for the async path."""
        deadline = time.monotonic() + self._total_timeout
        last_error: Exception | None = None
        for name, raw_model in self._ordered():
            model = self._bind(raw_model, tools, response_format)
            for attempt in range(self._max_retries):
                if time.monotonic() >= deadline:
                    break
                try:
                    return await model.ainvoke(messages)
                except Exception as exc:
                    last_error = exc
                    if is_non_retryable(exc):
                        raise
                    if attempt < self._max_retries - 1 and time.monotonic() < deadline:
                        delay = self._backoff(attempt)
                        if time.monotonic() + delay > deadline:
                            break
                        logger.warning("llm retry model=%s attempt=%d", name, attempt + 1)
                        await asyncio.sleep(delay)
            if time.monotonic() >= deadline:
                break
            logger.warning("llm fallback from model=%s", name)
        raise RuntimeError(f"MODEL_UNAVAILABLE: registry exhausted: {last_error}")

    @staticmethod
    def _bind(
        model: Any,
        tools: list[Any] | None,
        response_format: type[BaseModel] | None,
    ) -> Runnable[Any, Any]:
        bound: Any = model
        if tools:
            binder = getattr(bound, "bind_tools", None)
            if callable(binder):
                bound = binder(tools)
        if response_format is not None:
            chainer = getattr(bound, "with_structured_output", None)
            if callable(chainer):
                bound = chainer(response_format)
        return bound  # type: ignore[no-any-return]
