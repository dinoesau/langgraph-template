"""App factory: wires HTTP routes with settings, middleware, and logging."""

from fastapi import FastAPI
from starlette.types import Lifespan

from api.v1 import auth, chat, health
from core.config import get_settings
from core.middleware import RequestIDMiddleware, setup_logging
from services.database import agent_lifespan


def create_app(lifespan: Lifespan[FastAPI] = agent_lifespan) -> FastAPI:
    """Build the app with health plus chat and request-id handling."""
    settings = get_settings()
    setup_logging(settings.log_level)
    app = FastAPI(title="langgraph-template", version="0.1.0", lifespan=lifespan)
    app.add_middleware(RequestIDMiddleware)
    app.include_router(health.router, prefix="/api/v1")
    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(chat.router, prefix="/api/v1")
    return app


app = create_app()
