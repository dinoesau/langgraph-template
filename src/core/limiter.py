"""Rate limiting for auth endpoints: per-IP via slowapi.

In-memory per-process storage. With N workers the effective limit is N
times the configured rate. Authenticated routes beyond register plus login
are unlimited by design in this slice; see docs/configuration.md.
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
