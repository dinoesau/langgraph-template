"""App factory: wires HTTP routes with settings, middleware, and logging."""

from fastapi import FastAPI, Request
from slowapi.errors import RateLimitExceeded
from starlette.responses import JSONResponse
from starlette.types import Lifespan

from api.v1 import auth, chat, health
from core.config import get_settings
from core.limiter import limiter
from core.middleware import RequestIDMiddleware, setup_logging
from services.database import agent_lifespan


def _rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Return 429 with retry hint when an auth endpoint is over limit."""
    return JSONResponse(status_code=429, content={"detail": "Too many requests"})


def create_app(lifespan: Lifespan[FastAPI] = agent_lifespan) -> FastAPI:
    """Build the app with health plus chat and request-id handling."""
    settings = get_settings()
    setup_logging(settings.log_level)
    app = FastAPI(title="langgraph-template", version="0.1.0", lifespan=lifespan)
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)  # type: ignore[arg-type]
    app.add_middleware(RequestIDMiddleware)
    app.include_router(health.router, prefix="/api/v1")
    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(chat.router, prefix="/api/v1")
    return app


app = create_app()
