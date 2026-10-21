"""Rate limiting for auth endpoints: per-IP via slowapi.

Memory storage in dev and tests, shared Redis in prod when configured.
Reset helper keeps per-test isolation without touching internals elsewhere.
"""

import logging

from slowapi import Limiter
from slowapi.util import get_remote_address

logger = logging.getLogger("limiter")

limiter = Limiter(key_func=get_remote_address)


def reset_limiter() -> None:
    """Clear in-memory counters; tests call this for isolation."""
    try:
        limiter._storage.reset()
    except Exception:  # noqa: BLE001 - reset is best effort across backends
        logger.debug("limiter reset skipped")
