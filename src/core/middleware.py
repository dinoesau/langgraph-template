"""Request identifiers plus structured logs plus metrics plus debug profiles."""

import cProfile
import json
import logging
import pstats
import time
import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from typing import Any

from prometheus_client import Counter, Gauge, Histogram
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

CallNext = Callable[[Request], Awaitable[Response]]

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
request_path_var: ContextVar[str] = ContextVar("request_path", default="-")
session_id_var: ContextVar[str] = ContextVar("session_id", default="-")
user_id_var: ContextVar[str] = ContextVar("user_id", default="-")

http_requests_total = Counter(
    "http_requests_total",
    "Request count by method plus route plus status.",
    ["method", "route", "status"],
)
http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "Request latency by method plus route.",
    ["method", "route"],
)
llm_inference_duration_seconds = Histogram(
    "llm_inference_duration_seconds",
    "LLM latency by model.",
    ["model"],
)
db_connections = Gauge(
    "db_connections",
    "Active database connections.",
)

_STANDARD_ATTRS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
    }
)


def _extra_fields(record: logging.LogRecord) -> dict[str, object]:
    """Collect user fields passed via extra without standard record attrs."""
    extra: dict[str, object] = {}
    for key, value in record.__dict__.items():
        if key not in _STANDARD_ATTRS and not key.startswith("_"):
            extra[key] = value
    return extra


class JsonFormatter(logging.Formatter):
    """One JSON object per line with request context attached."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": request_id_var.get(),
            "session_id": session_id_var.get(),
            "user_id": user_id_var.get(),
            "path": request_path_var.get(),
        }
        payload.update(_extra_fields(record))
        return json.dumps(payload, default=str)


class PlainFormatter(logging.Formatter):
    """Readable console output for development with the same context."""

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        context = (
            f" request_id={request_id_var.get()}"
            f" session_id={session_id_var.get()}"
            f" user_id={user_id_var.get()}"
            f" path={request_path_var.get()}"
        )
        return base + context


def setup_logging(level: str, app_env: str = "production") -> None:
    """Configure stdout logging once at app startup."""
    handler = logging.StreamHandler()
    if app_env == "development":
        handler.setFormatter(PlainFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    else:
        handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)


def _normalize_path(raw: str) -> str:
    """Replace id-like segments so metric labels keep low cardinality."""
    parts = raw.split("/")
    out: list[str] = []
    hexdigits = set("0123456789abcdefABCDEF")
    for part in parts:
        if not part:
            out.append(part)
            continue
        if part.isdigit():
            out.append("{id}")
            continue
        clean = part.replace("-", "")
        if len(clean) >= 8 and all(c in hexdigits for c in clean):
            out.append("{id}")
            continue
        out.append(part)
    return "/".join(out)


def _route_template(request: Request) -> str:
    """Use the normalized full path so labels keep prefix plus low cardinality."""
    return _normalize_path(request.url.path)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Assign and echo a unique request identifier on every exchange."""

    async def dispatch(self, request: Request, call_next: CallNext) -> Response:
        from core.config import get_settings

        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request.state.request_id = request_id
        request_id_var.set(request_id)
        request_path_var.set(request.url.path)
        session_id_var.set("-")
        user_id_var.set("-")
        settings = get_settings()
        debug_on = bool(settings.debug)
        threshold = float(settings.slow_request_threshold_seconds)
        profiler: cProfile.Profile | None = None
        if debug_on:
            profiler = cProfile.Profile()
            profiler.enable()
        start = time.monotonic()
        api_logger = logging.getLogger("api")
        api_logger.info("http_request_received", extra={"method": request.method})
        status_code = 500
        response: Response | None = None
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-ID"] = request_id
            return response
        except Exception:
            api_logger.exception("http_request_failed")
            raise
        finally:
            duration = time.monotonic() - start
            if profiler is not None:
                profiler.disable()
            route = _route_template(request)
            request_path_var.set(route)
            labels_method = request.method
            http_requests_total.labels(
                method=labels_method, route=route, status=str(status_code)
            ).inc()
            http_request_duration_seconds.labels(method=labels_method, route=route).observe(
                duration
            )
            api_logger.info(
                "http_request_completed",
                extra={"method": labels_method, "route": route, "status": status_code},
            )
            if profiler is not None and duration >= threshold:
                _write_profile(request_id, route, duration, profiler)


def _write_profile(
    request_id: str, route: str, duration: float, profiler: cProfile.Profile
) -> None:
    """Save a small JSON profile joined by request identifier."""
    try:
        stream = StringIO()
        stats = pstats.Stats(profiler, stream=stream)
        stats.sort_stats("cumulative")
        raw_stats: dict[Any, Any] = getattr(stats, "stats", {})
        top: list[dict[str, object]] = []
        for func, stat in list(raw_stats.items())[:20]:
            file_name, line_no, func_name = func
            calls, _, total, cumulative, _ = stat[0], stat[1], stat[2], stat[3], stat[4]
            top.append(
                {
                    "func": f"{file_name}:{line_no}:{func_name}",
                    "calls": calls,
                    "total_time": total,
                    "cumulative_time": cumulative,
                }
            )
        payload = {
            "request_id": request_id,
            "route": route,
            "duration_seconds": duration,
            "profile": top,
        }
        out_dir = Path("profiles")
        out_dir.mkdir(exist_ok=True)
        (out_dir / f"{request_id}.json").write_text(json.dumps(payload, default=str))
    except Exception:  # noqa: BLE001 - profiling must never break the request
        logging.getLogger("api").debug("profile write skipped")
