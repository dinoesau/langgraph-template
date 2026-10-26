"""One model entry: retries per model, circular fallback, total budget.

Manual loop instead of ``with_fallbacks`` so start offset plus per-model
attempt counts stay explicit and testable. Tools rebind on every model
switch inside this module, so nodes never handle provider differences.
Structured output only applies as a terminal chain, never inside the tool
loop, because function-calling mode suppresses ``tool_calls``.

Error policy: unknown failures retry (transient-leaning), while auth,
validation, and client errors fail fast with no fallback. When both tools
and a response format are given, the format chains on top of the
tool-bound model; callers keep that combination terminal-only.
"""

import asyncio
import logging
import random
import time
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.runnables import Runnable
from pydantic import BaseModel

from services.llm.registry import build_registry, rotate

logger = logging.getLogger("llm")


def _trace_callbacks(metadata: dict[str, str] | None) -> list[Any]:
    """Build Langfuse callbacks when tracing is enabled with keys present."""
    try:
        from core.config import get_settings
    except Exception:  # noqa: BLE001 - tracing never breaks the call
        return []
    try:
        settings = get_settings()
    except Exception:  # noqa: BLE001 - tracing never breaks the call
        return []
    if not settings.langfuse_tracing_enabled:
        return []
    if not settings.langfuse_public_key.strip() or not settings.langfuse_secret_key.strip():
        return []
    try:
        from langfuse.langchain import CallbackHandler

        key = settings.langfuse_public_key.strip() or None
        handler = CallbackHandler(public_key=key)
        return [handler]
    except Exception:  # noqa: BLE001 - missing dep or bad keys disables tracing
        logger.debug("langfuse handler disabled", extra={"reason": "init_failed"})
        return []


def _resolve_metadata(explicit: dict[str, str] | None) -> dict[str, str]:
    """Merge explicit trace context with the request ContextVar fallback."""
    data: dict[str, str] = dict(explicit or {})
    if not data.get("request_id"):
        try:
            from core.middleware import request_id_var

            data["request_id"] = request_id_var.get()
        except Exception:  # noqa: BLE001 - studio path stays without request id
            data["request_id"] = "-"
    data.setdefault("session_id", "-")
    return data


def _extract_usage(result: Any) -> dict[str, object]:
    """Pull token usage from the model reply without failing the turn."""
    for attr in ("usage_metadata", "usage", "response_metadata"):
        try:
            value = getattr(result, attr, None)
        except Exception as exc:  # noqa: BLE001 - usage is best effort
            logger.debug("usage read skipped", extra={"attr": attr, "error": str(exc)})
            continue
        if isinstance(value, dict) and value:
            if attr == "response_metadata":
                nested = value.get("token_usage")
                if isinstance(nested, dict):
                    return {"token_usage": nested}
                continue
            return {attr: value}
    return {}


def _invoke_sync(model: Any, messages: list[BaseMessage], run_config: dict[str, Any]) -> Any:
    """Invoke with trace config when supported, else fall back to plain invoke."""
    try:
        return model.invoke(messages, config=run_config)
    except TypeError:
        return model.invoke(messages)


async def _ainvoke_async(
    model: Any, messages: list[BaseMessage], run_config: dict[str, Any]
) -> Any:
    """Async invoke with trace config when supported, else plain ainvoke."""
    try:
        return await model.ainvoke(messages, config=run_config)
    except TypeError:
        return await model.ainvoke(messages)


def _observe_llm(model_name: str, latency: float) -> None:
    """Record LLM latency without breaking the turn when metrics are down."""
    try:
        from core.middleware import llm_inference_duration_seconds

        llm_inference_duration_seconds.labels(model=model_name).observe(max(0.0, latency))
    except Exception:  # noqa: BLE001 - metrics never break the model call
        logger.debug("llm metric skipped", extra={"model": model_name})


def _log_trace(
    model_name: str,
    messages: list[BaseMessage],
    result: Any,
    latency: float,
    trace_meta: dict[str, str],
) -> None:
    """Emit one structured trace line with inputs plus outputs plus usage."""
    try:
        inputs = [getattr(m, "content", "") for m in messages]
        output = getattr(result, "content", result)
        usage = _extract_usage(result)
        logger.info(
            "llm_call_completed",
            extra={
                "model": model_name,
                "latency_seconds": latency,
                "request_id": trace_meta.get("request_id", "-"),
                "session_id": trace_meta.get("session_id", "-"),
                "inputs": inputs,
                "output": output,
                "usage": usage,
            },
        )
    except Exception:  # noqa: BLE001 - tracing never breaks the call
        logger.debug("llm trace skipped", extra={"model": model_name})


def is_retryable(error: Exception) -> bool:
    """Classify provider failures; auth and validation fail fast.

    Anything unrecognized retries on purpose: a new provider error shape
    should degrade into the fallback chain, not into a hard failure.
    """
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
    logger.debug("llm_unknown_error_shape", extra={"error": str(error)})
    return True


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
            return rotate(self._models, names.index(self._start))
        return list(self._models)

    def _compute_delay(self, attempt: int, deadline: float) -> float | None:
        """Jittered backoff for the next retry, or None when the budget stops us."""
        if attempt >= self._max_retries - 1 or time.monotonic() >= deadline:
            return None
        delay = min(self._backoff_max, self._backoff_min * (2**attempt))
        if delay <= 0:
            return 0.0
        jittered = delay * random.uniform(0.9, 1.1)
        if time.monotonic() + jittered > deadline:
            return None
        return float(jittered)

    def call(
        self,
        messages: list[BaseMessage],
        tools: list[Any] | None = None,
        response_format: type[BaseModel] | None = None,
        metadata: dict[str, str] | None = None,
    ) -> Any:
        """Sync entry; retries one model then walks the registry once."""
        deadline = time.monotonic() + self._total_timeout
        last_error: Exception | None = None
        trace_meta = _resolve_metadata(metadata)
        callbacks = _trace_callbacks(trace_meta)
        for name, raw_model in self._ordered():
            for attempt in range(self._max_retries):
                if time.monotonic() >= deadline:
                    break
                model = self._bind(raw_model, tools, response_format)
                run_config: dict[str, Any] = {"metadata": {**trace_meta, "model": name}}
                if callbacks:
                    run_config["callbacks"] = callbacks
                started = time.monotonic()
                try:
                    result = _invoke_sync(model, messages, run_config)
                    latency = time.monotonic() - started
                    _observe_llm(name, latency)
                    _log_trace(name, messages, result, latency, trace_meta)
                    return result
                except Exception as exc:
                    last_error = exc
                    if not is_retryable(exc):
                        raise
                    delay = self._compute_delay(attempt, deadline)
                    if delay is None:
                        break
                    logger.warning(
                        "llm_retry",
                        extra={"model": name, "attempt": attempt + 1, "error": str(exc)},
                    )
                    time.sleep(delay)
            if time.monotonic() >= deadline:
                break
            logger.warning("llm_fallback", extra={"model": name})
        raise RuntimeError(f"MODEL_UNAVAILABLE: registry exhausted: {last_error}")

    async def acall(
        self,
        messages: list[BaseMessage],
        tools: list[Any] | None = None,
        response_format: type[BaseModel] | None = None,
        metadata: dict[str, str] | None = None,
    ) -> Any:
        """Async entry; same semantics as the sync entry for the async path."""
        deadline = time.monotonic() + self._total_timeout
        last_error: Exception | None = None
        trace_meta = _resolve_metadata(metadata)
        callbacks = _trace_callbacks(trace_meta)
        for name, raw_model in self._ordered():
            for attempt in range(self._max_retries):
                if time.monotonic() >= deadline:
                    break
                model = self._bind(raw_model, tools, response_format)
                run_config: dict[str, Any] = {"metadata": {**trace_meta, "model": name}}
                if callbacks:
                    run_config["callbacks"] = callbacks
                started = time.monotonic()
                try:
                    result = await _ainvoke_async(model, messages, run_config)
                    latency = time.monotonic() - started
                    _observe_llm(name, latency)
                    _log_trace(name, messages, result, latency, trace_meta)
                    return result
                except Exception as exc:
                    last_error = exc
                    if not is_retryable(exc):
                        raise
                    delay = self._compute_delay(attempt, deadline)
                    if delay is None:
                        break
                    logger.warning(
                        "llm_retry",
                        extra={"model": name, "attempt": attempt + 1, "error": str(exc)},
                    )
                    await asyncio.sleep(delay)
            if time.monotonic() >= deadline:
                break
            logger.warning("llm_fallback", extra={"model": name})
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
