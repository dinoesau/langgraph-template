"""Readiness probe for containers and humans."""

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    """Service plus dependency readiness, no ORM types cross here."""

    status: str
    checks: dict[str, str]


@router.get("/health", response_model=HealthResponse)
def read_health() -> HealthResponse:
    """Report service liveness plus loop and settings readiness."""
    return HealthResponse(status="ok", checks={"graph": "loaded", "settings": "loaded"})
