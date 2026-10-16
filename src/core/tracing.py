"""S6 LLM tracing: one record per model attempt, eval-ready, offline flag.

Every record carries inputs, output text, token usage, latency, and identity
(request, thread/session, user) so traces can feed the eval runner as input.
No network calls happen here: LangSmith/Langfuse stay stubbed and the only
sinks are the in-memory buffer plus an optional JSONL file. Tracing turns off
without a code change through ``LANGFUSE_TRACING_ENABLED=false``.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langchain_core.messages import BaseMessage

logger = logging.getLogger(__name__)

_MAX_BUFFER = 500


@dataclass
class TraceRecord:
    """One traced model attempt with identity for eval input."""

    trace_id: str
    timestamp: str
    model: str
    inputs: list[dict[str, str]]
    output: str
    usage: dict[str, int]
    latency_ms: float
    request_id: str = ""
    thread_id: str = ""
    user_id: str = ""
    error: str | None = None


_buffer: list[TraceRecord] = []


def tracing_enabled() -> bool:
    """Return False when offline tracing is disabled through the environment."""
    from src.core.config import get_settings

    return get_settings().LANGFUSE_TRACING_ENABLED


def messages_to_inputs(messages: Sequence[BaseMessage]) -> list[dict[str, str]]:
    """Serialize messages to role/content pairs for trace records."""
    inputs: list[dict[str, str]] = []
    for message in messages:
        role = str(getattr(message, "type", "unknown"))
        inputs.append({"role": role, "content": message_to_text(message)})
    return inputs


def message_to_text(message: BaseMessage) -> str:
    """Render message content, joining multimodal blocks when needed."""
    content = message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(str(block["text"]))
        return "".join(parts)
    return str(content)


def extract_output_text(result: Any) -> str:
    """Render a model result to plain text for trace and eval input."""
    if isinstance(result, BaseMessage):
        return message_to_text(result)
    content = getattr(result, "content", None)
    if isinstance(content, str):
        return content
    return str(result)


def extract_usage(result: Any) -> dict[str, int]:
    """Pull token usage off a result, defaulting every counter to zero."""
    usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    meta = getattr(result, "usage_metadata", None)
    if isinstance(meta, dict):
        for key in usage:
            value = meta.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                usage[key] = value
        return usage
    response_meta = getattr(result, "response_metadata", None)
    token_usage = response_meta.get("token_usage") if isinstance(response_meta, dict) else None
    if isinstance(token_usage, dict):
        mapping = {
            "input_tokens": ("prompt_tokens", "input_tokens"),
            "output_tokens": ("completion_tokens", "output_tokens"),
            "total_tokens": ("total_tokens",),
        }
        for target, sources in mapping.items():
            for source in sources:
                value = token_usage.get(source)
                if isinstance(value, int) and not isinstance(value, bool):
                    usage[target] = value
                    break
    return usage


def record_trace(
    *,
    model: str,
    inputs: list[dict[str, str]],
    output: str,
    usage: dict[str, int],
    latency_ms: float,
    request_id: str = "",
    thread_id: str = "",
    user_id: str = "",
    error: str | None = None,
) -> TraceRecord | None:
    """Append one trace record, returning None when tracing is disabled."""
    if not tracing_enabled():
        return None
    record = TraceRecord(
        trace_id=uuid.uuid4().hex,
        timestamp=datetime.now(tz=UTC).isoformat(),
        model=model,
        inputs=inputs,
        output=output,
        usage=dict(usage),
        latency_ms=latency_ms,
        request_id=request_id,
        thread_id=thread_id,
        user_id=user_id,
        error=error,
    )
    _buffer.append(record)
    if len(_buffer) > _MAX_BUFFER:
        del _buffer[: len(_buffer) - _MAX_BUFFER]
    _maybe_persist(record)
    return record


def recent_traces(limit: int = 50) -> list[TraceRecord]:
    """Return the newest buffered traces, newest last, for eval input."""
    if limit <= 0:
        return []
    return list(_buffer[-limit:])


def clear_traces() -> None:
    """Empty the in-memory buffer, used by tests and eval harnesses."""
    _buffer.clear()


def to_eval_record(record: TraceRecord) -> dict[str, object]:
    """Convert one trace to the eval runner input shape."""
    payload: dict[str, object] = {
        "trace_id": record.trace_id,
        "timestamp": record.timestamp,
        "model": record.model,
        "input": [dict(item) for item in record.inputs],
        "output": record.output,
        "usage": dict(record.usage),
        "latency_ms": record.latency_ms,
        "request_id": record.request_id,
        "thread_id": record.thread_id,
        "user_id": record.user_id,
    }
    if record.error is not None:
        payload["error"] = record.error
    return payload


def _maybe_persist(record: TraceRecord) -> None:
    """Append one JSONL line when TRACE_LOG_PATH is configured."""
    from src.core.config import get_settings

    path = get_settings().TRACE_LOG_PATH.strip()
    if not path:
        return
    try:
        target = Path(path)
        if target.parent != Path("."):
            target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(record)) + "\n")
    except OSError:
        logger.warning("trace_persist_failed", exc_info=True)
