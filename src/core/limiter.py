"""Brute-force rate limits for the auth endpoints.

In-memory sliding window keyed by client plus route; documented in
``docs/authentication.md``. A shared cache backend can replace this module
without changing the route dependencies.
"""

import threading
import time
from collections import deque
from collections.abc import Callable

from fastapi import Request


class RateLimiter:
    """Allow ``max_attempts`` checks per ``window_seconds`` for each key."""

    def __init__(
        self,
        max_attempts: int = 20,
        window_seconds: int = 60,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_attempts = max_attempts
        self._window = window_seconds
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        """Record one attempt; raise PermissionError when the key is over budget."""
        now = self._clock()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= now - self._window:
                hits.popleft()
            if len(hits) >= self._max_attempts:
                raise PermissionError("Rate limit exceeded, try again later.")
            hits.append(now)

    def reset(self) -> None:
        """Clear all counters; used by tests for isolation."""
        with self._lock:
            self._hits.clear()


auth_rate_limiter = RateLimiter(max_attempts=20, window_seconds=60)


def client_key(request: Request) -> str:
    """Identify the caller by IP plus route for brute-force budgeting."""
    host = request.client.host if request.client is not None else "unknown"
    return f"{host}:{request.url.path}"
