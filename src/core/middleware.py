import json
import logging
import sys
import time
import uuid
from datetime import UTC, datetime

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger("api.access")


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
    """Assign X-Request-ID, log one JSON line, and echo the header."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming = request.headers.get("X-Request-ID")
        request_id = incoming if incoming else uuid.uuid4().hex
        request.state.request_id = request_id
        thread_id = await _resolve_log_thread_id(request)
        request.state.thread_id = thread_id
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "http_request_failed",
                extra={
                    "request_id": request_id,
                    "thread_id": thread_id,
                    "method": request.method,
                    "path": request.url.path,
                },
            )
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
                "request_id": request_id,
                "thread_id": thread_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        return response
