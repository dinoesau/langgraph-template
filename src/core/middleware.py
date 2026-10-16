import json
import logging
import sys
import time
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from src.core.metrics import observe_http
from src.core.profiler import write_profile

logger = logging.getLogger("api.access")

_request_id_ctx: ContextVar[str] = ContextVar("request_id", default="")
_thread_id_ctx: ContextVar[str] = ContextVar("thread_id", default="")


def set_log_context(*, request_id: str, thread_id: str) -> None:
    """Bind request plus session context for every log line on this task."""
    _request_id_ctx.set(request_id)
    _thread_id_ctx.set(thread_id)


def clear_log_context() -> None:
    """Drop the bound context, used at the end of each request."""
    _request_id_ctx.set("")
    _thread_id_ctx.set("")


def get_request_id() -> str:
    """Return the bound request id, or empty when outside a request."""
    return _request_id_ctx.get()


def get_thread_id() -> str:
    """Return the bound thread id, or empty when outside a request."""
    return _thread_id_ctx.get()


class ContextFilter(logging.Filter):
    """Stamp request plus session context onto records as fields."""

    def filter(self, record: logging.LogRecord) -> bool:
        request_id = get_request_id()
        thread_id = get_thread_id()
        if request_id and not hasattr(record, "request_id"):
            record.request_id = request_id
        if thread_id and not hasattr(record, "thread_id"):
            record.thread_id = thread_id
        return True


def _install_record_factory() -> None:
    """Stamp context at record creation so every logger carries the fields."""
    factory = logging.getLogRecordFactory()

    def _context_factory(*args: object, **kwargs: object) -> logging.LogRecord:
        record = factory(*args, **kwargs)
        ContextFilter().filter(record)
        return record

    logging.setLogRecordFactory(_context_factory)


_install_record_factory()


class JsonFormatter(logging.Formatter):
    """Format records as single-line JSON for stdout logs."""

    def format(self, record: logging.LogRecord) -> str:
        data: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        skip = {
            "args",
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
        for key, value in record.__dict__.items():
            if key not in skip and not key.startswith("_"):
                data[key] = value
        if record.exc_info:
            data["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(data)


if not logger.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(JsonFormatter())
    logger.addHandler(_handler)
logger.setLevel(logging.INFO)
logger.propagate = False


async def _resolve_log_thread_id(request: Request) -> str:
    """Read thread_id from the JSON body when present, else generate one."""
    try:
        body = await request.body()
    except OSError:
        return uuid.uuid4().hex
    if body:
        try:
            data: object = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return uuid.uuid4().hex
        if isinstance(data, dict):
            candidate = data.get("thread_id")
            if isinstance(candidate, str) and candidate:
                return candidate
    return uuid.uuid4().hex


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assign X-Request-ID, log one JSON line, and echo the header.

    Extends the S1 middleware in place: the same dispatch now binds
    request plus session context for every log line, counts the request in
    the Prometheus series, and writes a debug-only slow profile named by
    request id. No fork, same header contract.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        from src.core.config import get_settings

        incoming = request.headers.get("X-Request-ID")
        request_id = incoming if incoming else uuid.uuid4().hex
        request.state.request_id = request_id
        thread_id = await _resolve_log_thread_id(request)
        request.state.thread_id = thread_id
        set_log_context(request_id=request_id, thread_id=thread_id)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "http_request_failed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                },
            )
            observe_http(request.method, request.url.path, 500, time.perf_counter() - start)
            clear_log_context()
            payload = {
                "error": {"code": "internal_error", "message": "Internal server error"},
                "request_id": request_id,
            }
            return JSONResponse(
                status_code=500, content=payload, headers={"X-Request-ID": request_id}
            )
        response.headers["X-Request-ID"] = request_id
        duration_ms = round((time.perf_counter() - start) * 1000, 3)
        logger.info(
            "http_request_completed",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        observe_http(request.method, request.url.path, response.status_code, duration_ms / 1000.0)
        settings = get_settings()
        try:
            write_profile(
                request_id=request_id,
                method=request.method,
                path=request.url.path,
                duration_ms=duration_ms,
                thread_id=thread_id,
                directory=settings.PROFILE_DIR,
                debug=settings.DEBUG,
                threshold_ms=settings.SLOW_REQUEST_THRESHOLD_MS,
            )
        except Exception:
            logger.warning("slow_profile_failed", exc_info=True)
        finally:
            clear_log_context()
        return response
