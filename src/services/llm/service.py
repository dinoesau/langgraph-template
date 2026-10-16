"""Single LLM call entry with bounded retries and circular fallback.

Each model gets MAX_LLM_CALL_RETRIES attempts with 2s to 10s exponential
backoff on retryable errors only (rate limit, timeout, API errors).
Non-retryable errors fail fast with no fallback. The walk covers the
registry once in circular order under LLM_TOTAL_TIMEOUT, then raises
RuntimeError. Tools rebind and structured output rechains on every switch.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage

from src.core.config import get_settings
from src.services.llm.registry import default_factories, ordered_names

logger = logging.getLogger(__name__)

_BASE_DELAY = 2.0
_MAX_DELAY = 10.0

_RETRYABLE_NAMES = frozenset(
    {
        "RateLimitError",
        "APITimeoutError",
        "APIConnectionError",
        "InternalServerError",
        "ServiceUnavailableError",
        "ServiceUnavailable",
        "OverloadedError",
        "Overloaded",
        "ResourceExhausted",
        "DeadlineExceeded",
        "BadGateway",
        "GatewayTimeout",
        "Timeout",
        "TimeoutException",
        "TimeoutError",
    }
)


def is_retryable(exc: BaseException) -> bool:
    """Return True for rate-limit, timeout, and API errors only."""
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    return type(exc).__name__ in _RETRYABLE_NAMES


def backoff_delay(retry_index: int) -> float:
    """Return the bounded exponential delay for a zero-based retry index."""
    return min(_BASE_DELAY * (2.0**retry_index), _MAX_DELAY)


SleepFn = Callable[[float], Awaitable[None]]


class LLMService:
    """One call entry over an ordered model registry."""

    def __init__(
        self,
        models: Mapping[str, BaseChatModel | Callable[[], BaseChatModel]] | None = None,
        *,
        max_retries: int | None = None,
        total_timeout: float | None = None,
        sleep: SleepFn = asyncio.sleep,
    ) -> None:
        settings = get_settings()
        self._factories: dict[str, Callable[[], BaseChatModel]] = {}
        for name, entry in (models if models is not None else default_factories()).items():
            if isinstance(entry, BaseChatModel):
                self._factories[name] = _ready(entry)
            else:
                self._factories[name] = entry
        self._max_retries = (
            max_retries if max_retries is not None else settings.MAX_LLM_CALL_RETRIES
        )
        self._total_timeout = (
            total_timeout if total_timeout is not None else settings.LLM_TOTAL_TIMEOUT
        )
        self._sleep = sleep
        self._tools: list[Any] = []

    def bind_tools(self, tools: Sequence[Any]) -> None:
        """Register tools once; they rebind on every model and every switch."""
        self._tools = list(tools)

    async def call(
        self,
        messages: Sequence[BaseMessage],
        *,
        model_name: str | None = None,
        response_format: Any | None = None,
        **kwargs: Any,
    ) -> Any:
        """Run one model call with retries and fallback under a total budget."""
        try:
            return await asyncio.wait_for(
                self._run(
                    messages, model_name=model_name, response_format=response_format, kwargs=kwargs
                ),
                timeout=self._total_timeout,
            )
        except TimeoutError as exc:
            raise RuntimeError(
                f"LLM call exceeded total timeout of {self._total_timeout}s"
            ) from exc

    async def _run(
        self,
        messages: Sequence[BaseMessage],
        *,
        model_name: str | None,
        response_format: Any | None,
        kwargs: dict[str, Any],
    ) -> Any:
        names = ordered_names(model_name, list(self._factories))
        errors: list[str] = []
        attempted = False
        for name in names:
            try:
                model = self._factories[name]()
            except (RuntimeError, ImportError) as exc:
                logger.warning("skipping unbuildable model %s: %s", name, exc)
                errors.append(f"{name}: {exc}")
                continue
            attempted = True
            try:
                return await self._call_model(name, model, messages, response_format, kwargs)
            except _ModelExhausted as exc:
                errors.append(f"{name}: {exc.last_error}")
                continue
        if not attempted:
            raise RuntimeError(f"No LLM model could be built: {'; '.join(errors)}")
        raise RuntimeError(f"LLM call failed, all models failed: {'; '.join(errors)}")

    async def _call_model(
        self,
        name: str,
        model: BaseChatModel,
        messages: Sequence[BaseMessage],
        response_format: Any | None,
        kwargs: dict[str, Any],
    ) -> Any:
        bound: Any = model
        if response_format is not None:
            bound = model.with_structured_output(response_format)
        if self._tools and hasattr(bound, "bind_tools"):
            bound = bound.bind_tools(self._tools)
        last_error: BaseException | None = None
        for attempt in range(self._max_retries):
            try:
                return await bound.ainvoke(list(messages), **kwargs)
            except Exception as exc:
                if not is_retryable(exc):
                    raise
                last_error = exc
                if attempt < self._max_retries - 1:
                    await self._sleep(backoff_delay(attempt))
        raise _ModelExhausted(name, last_error)


class _ModelExhausted(Exception):
    def __init__(self, name: str, last_error: BaseException | None) -> None:
        super().__init__(f"{name} exhausted retries")
        self.last_error = last_error


def _ready(model: BaseChatModel) -> Callable[[], BaseChatModel]:
    return lambda: model


llm_service = LLMService()
