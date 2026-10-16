"""S6 observability seam: traces, logs, metrics, profiler.

Covers the four acceptance signals without external backends:
- trace records carry inputs/outputs/usage/latency/identity and convert to eval input
- JSON log lines carry request plus session context as fields, never interpolated
- GET /metrics exposes request plus model plus store signals in Prometheus text
- the debug-only slow profiler gates on threshold and names files by request id
"""

import json
import logging
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from src.core.config import get_settings


class _FakeModel(BaseChatModel):
    """Single-shot fake model with usage metadata, no network calls."""

    text: str = "traced-reply"

    @property
    def _llm_type(self) -> str:
        return "s6-fake"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        message = AIMessage(
            content=self.text,
            usage_metadata={"input_tokens": 5, "output_tokens": 7, "total_tokens": 12},
        )
        return ChatResult(generations=[ChatGeneration(message=message)])


def _clear_settings_cache() -> None:
    get_settings.cache_clear()


def test_trace_records_inputs_outputs_usage_latency_identity() -> None:
    from src.core import tracing
    from src.services.llm.service import LLMService

    tracing.clear_traces()
    service = LLMService(models={"fake": _FakeModel()}, sleep=_never_sleep())
    result = _run(
        service.call(
            [HumanMessage(content="hello trace")],
            request_id="req-trace-1",
            user_id="user-1",
            thread_id="thread-1",
        )
    )
    assert isinstance(result, AIMessage)
    records = tracing.recent_traces()
    assert len(records) == 1
    record = records[0]
    assert record.model == "fake"
    assert any("hello trace" in item["content"] for item in record.inputs)
    assert "traced-reply" in record.output
    assert record.usage["input_tokens"] == 5
    assert record.usage["output_tokens"] == 7
    assert record.usage["total_tokens"] == 12
    assert record.latency_ms >= 0.0
    assert record.request_id == "req-trace-1"
    assert record.user_id == "user-1"
    assert record.thread_id == "thread-1"


def test_trace_disabled_flag_records_nothing(monkeypatch: Any, tmp_path: Any) -> None:
    from src.core import tracing
    from src.services.llm.service import LLMService

    monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "false")
    _clear_settings_cache()
    try:
        tracing.clear_traces()
        service = LLMService(models={"fake": _FakeModel()}, sleep=_never_sleep())
        _run(service.call([HumanMessage(content="hi")], request_id="req-off"))
        assert tracing.recent_traces() == []
    finally:
        _clear_settings_cache()
    assert tmp_path.exists()


def test_trace_converts_to_eval_input() -> None:
    from src.core import tracing
    from src.services.llm.service import LLMService

    tracing.clear_traces()
    service = LLMService(models={"fake": _FakeModel()}, sleep=_never_sleep())
    _run(service.call([HumanMessage(content="eval me")], request_id="req-eval"))
    (record,) = tracing.recent_traces()
    eval_record = tracing.to_eval_record(record)
    assert eval_record["input"]
    assert "traced-reply" in str(eval_record["output"])
    assert eval_record["model"] == "fake"
    assert "usage" in eval_record
    assert "trace_id" in eval_record


def test_json_log_line_carries_context_as_fields() -> None:
    from src.core.middleware import JsonFormatter

    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="api.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="http_request_completed",
        args=(),
        exc_info=None,
    )
    record.request_id = "req-log-1"
    record.thread_id = "thread-log-1"
    line = formatter.format(record)
    payload = json.loads(line)
    assert payload["message"] == "http_request_completed"
    assert payload["request_id"] == "req-log-1"
    assert payload["thread_id"] == "thread-log-1"
    assert "req-log-1" not in payload["message"]


def test_middleware_binds_context_to_every_line() -> None:
    from src.core import middleware

    middleware.set_log_context(request_id="req-ctx-1", thread_id="thread-ctx-1")
    try:
        record = logging.LogRecord(
            name="anything",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="some_event",
            args=(),
            exc_info=None,
        )
        middleware.ContextFilter().filter(record)
        assert getattr(record, "request_id", None) == "req-ctx-1"
        assert getattr(record, "thread_id", None) == "thread-ctx-1"
    finally:
        middleware.clear_log_context()


def test_metrics_endpoint_shape() -> None:
    from fastapi.testclient import TestClient

    from src.api.app import create_app

    client = TestClient(create_app())
    client.get("/health")
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    body = response.text
    assert "http_requests_total" in body
    assert "http_request_duration_seconds" in body
    assert "llm_inference_duration_seconds" in body
    assert "db_connections" in body


def test_metrics_buckets_are_cumulative() -> None:
    from src.core import metrics

    metrics.reset_metrics()
    try:
        metrics.observe_http("GET", "/health", 200, 0.012)
        text = metrics.render_text()
        assert (
            'http_request_duration_seconds_bucket{le="0.01",method="GET",path="/health"} 0' in text
        )
        assert (
            'http_request_duration_seconds_bucket{le="0.025",method="GET",path="/health"} 1' in text
        )
        assert (
            'http_request_duration_seconds_bucket{le="+Inf",method="GET",path="/health"} 1' in text
        )
    finally:
        metrics.reset_metrics()


def test_profiler_threshold_and_request_id_naming(tmp_path: Any) -> None:
    from src.core import profiler

    slow = profiler.write_profile(
        request_id="req-slow-1",
        method="POST",
        path="/api/v1/chat",
        duration_ms=2500.0,
        thread_id="thread-slow-1",
        directory=str(tmp_path),
        debug=True,
        threshold_ms=1000.0,
    )
    assert slow is not None
    assert slow.name == "req-slow-1.json"
    payload = json.loads(slow.read_text())
    assert payload["request_id"] == "req-slow-1"
    assert payload["duration_ms"] == 2500.0

    fast = profiler.write_profile(
        request_id="req-fast-1",
        method="GET",
        path="/health",
        duration_ms=5.0,
        thread_id="thread-fast-1",
        directory=str(tmp_path),
        debug=True,
        threshold_ms=1000.0,
    )
    assert fast is None
    assert not (tmp_path / "req-fast-1.json").exists()

    disabled = profiler.write_profile(
        request_id="req-dbg-off",
        method="GET",
        path="/health",
        duration_ms=5000.0,
        thread_id="thread-x",
        directory=str(tmp_path),
        debug=False,
        threshold_ms=1000.0,
    )
    assert disabled is None


class _Sleeper:
    async def __call__(self, delay: float) -> None:
        raise AssertionError("sleep must not run on the happy path")


def _never_sleep() -> _Sleeper:
    return _Sleeper()


def _run(coro: Any) -> Any:
    import asyncio

    return asyncio.run(coro)
