"""Request identifiers plus structured JSON logs for every HTTP exchange."""

import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from datetime import UTC, datetime

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

CallNext = Callable[[Request], Awaitable[Response]]

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
request_path_var: ContextVar[str] = ContextVar("request_path", default="-")


class JsonFormatter(logging.Formatter):
    """One JSON object per line with request context attached."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "msg": record.getMessage(),
            "request_id": request_id_var.get(),
            "path": request_path_var.get(),
        }
        return json.dumps(payload)


def setup_logging(level: str) -> None:
    """Configure stdout JSON logging once at app startup."""
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Assign and echo a unique request identifier on every exchange."""

    async def dispatch(self, request: Request, call_next: CallNext) -> Response:
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request.state.request_id = request_id
        request_id_var.set(request_id)
        request_path_var.set(request.url.path)
        logging.getLogger("api").info(f"{request.method} {request.url.path}")
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
