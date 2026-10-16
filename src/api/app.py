import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.api.v1 import router as v1_router
from src.core.metrics import render_text, set_db_connections
from src.core.middleware import RequestContextMiddleware
from src.services.database import cache_status, database_lifespan, store_status

Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[Any, Any]]


def _get_request_id(request: Request) -> str:
    state_id = getattr(request.state, "request_id", None)
    if isinstance(state_id, str) and state_id:
        return state_id
    incoming = request.headers.get("X-Request-ID")
    if incoming:
        return incoming
    return uuid.uuid4().hex


def _error_response(status_code: int, code: str, message: str, request_id: str) -> JSONResponse:
    payload = {"error": {"code": code, "message": message}, "request_id": request_id}
    return JSONResponse(
        status_code=status_code, content=payload, headers={"X-Request-ID": request_id}
    )


def create_app(lifespan: Lifespan = database_lifespan) -> FastAPI:
    """Build the API with health, Prometheus metrics, chat routes, and store lifespan."""
    app = FastAPI(title="langgraph-template", lifespan=lifespan)
    app.add_middleware(RequestContextMiddleware)
    app.include_router(v1_router)

    @app.get("/health")
    def health(request: Request) -> dict[str, object]:
        return {
            "status": "ok",
            "dependencies": {
                "loop": "ok",
                "store": store_status(request.app),
                "cache": cache_status(request.app),
            },
        }

    @app.get("/metrics")
    def metrics(request: Request) -> Response:
        """Expose request, model, and store signals in Prometheus text format."""
        checkpointer = getattr(request.app.state, "checkpointer", None)
        set_db_connections(1 if checkpointer is not None else 0)
        return Response(
            content=render_text(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        request_id = _get_request_id(request)
        return _error_response(422, "validation_error", str(exc), request_id)

    @app.exception_handler(StarletteHTTPException)
    async def http_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        request_id = _get_request_id(request)
        code = "not_found" if exc.status_code == 404 else "http_error"
        message = str(exc.detail) if exc.detail else "HTTP error"
        return _error_response(exc.status_code, code, message, request_id)

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        request_id = _get_request_id(request)
        return _error_response(500, "internal_error", "Internal server error", request_id)

    return app


app = create_app()
