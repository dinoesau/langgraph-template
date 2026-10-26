"""Ops metrics: Prometheus scrape target without auth."""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from core.middleware import db_connections

router = APIRouter(tags=["metrics"])
logger = logging.getLogger("metrics")


def _pool_connections(request: Request) -> int:
    """Read checked-out connections, zero when ephemeral or unreadable."""
    try:
        engine = getattr(request.app.state, "auth_engine", None)
        pool = getattr(engine, "pool", None)
        checked = getattr(pool, "checkedout", None)
        if callable(checked):
            value = checked()
            return int(value) if isinstance(value, int) else 0
    except Exception:  # noqa: BLE001 - metrics never break the scrape
        logger.debug("db pool read skipped")
    return 0


@router.get("/metrics")
def read_metrics(request: Request) -> Response:
    """Expose request plus model plus store signals for Prometheus."""
    db_connections.set(_pool_connections(request))
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
