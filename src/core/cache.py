"""S2b cache front: shared Valkey with in-memory TTL fallback.

Contract (per docs/memory.md): key ``memory:{user_id}:{query_hash}``,
TTL from ``CACHE_TTL_SECONDS`` (default 60s), only successful non-empty
results are cached, errors are never cached. Without ``VALKEY_HOST`` the
code uses the single-instance in-memory TTL cache.
"""

import hashlib
import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


def memory_key(user_id: str, query: str) -> str:
    """Build the shared cache key for one user query."""
    digest = hashlib.sha256(query.encode()).hexdigest()[:16]
    return f"memory:{user_id}:{digest}"


@dataclass
class _Entry:
    value: str
    expires_at: float


@dataclass
class InMemoryCache:
    """Single-instance TTL cache used when Valkey is not configured."""

    _items: dict[str, _Entry] = field(default_factory=dict)

    def get(self, key: str) -> str | None:
        entry = self._items.get(key)
        if entry is None:
            return None
        if entry.expires_at <= time.monotonic():
            del self._items[key]
            return None
        return entry.value

    def set(self, key: str, value: str, ttl_seconds: int = 60) -> None:
        self._items[key] = _Entry(value=value, expires_at=time.monotonic() + ttl_seconds)


class ValkeyCache:
    """Redis-protocol shared cache (works against Valkey) with memory fallback.

    Every operation degrades to the in-memory cache when the client is
    missing or unreachable, so chat never fails because the cache is down.
    """

    def __init__(self, host: str, port: int = 6379, ttl_seconds: int = 60) -> None:
        self._ttl = ttl_seconds
        self._fallback = InMemoryCache()
        self._client: object | None = None
        try:
            import redis

            self._client = redis.Redis(host=host, port=port, socket_timeout=2)
        except Exception:
            logger.warning("valkey client unavailable, using in-memory cache", exc_info=True)
            self._client = None

    @property
    def shared(self) -> bool:
        """True when a shared Valkey client is configured, else in-memory fallback."""
        return self._client is not None

    def get(self, key: str) -> str | None:
        if self._client is None:
            return self._fallback.get(key)
        try:
            import redis

            assert isinstance(self._client, redis.Redis)
            raw = self._client.get(key)
        except Exception:
            logger.warning("valkey get failed, using fallback", exc_info=True)
            return self._fallback.get(key)
        if raw is None:
            return None
        if isinstance(raw, bytes):
            return raw.decode("utf-8")
        return str(raw)

    def set(self, key: str, value: str, ttl_seconds: int | None = None) -> None:
        ttl = ttl_seconds if ttl_seconds is not None else self._ttl
        if self._client is None:
            self._fallback.set(key, value, ttl_seconds=ttl)
            return
        try:
            import redis

            assert isinstance(self._client, redis.Redis)
            self._client.setex(key, ttl, value)
        except Exception:
            logger.warning("valkey set failed, using fallback", exc_info=True)
            self._fallback.set(key, value, ttl_seconds=ttl)


def make_cache(host: str, port: int = 6379, ttl_seconds: int = 60) -> InMemoryCache | ValkeyCache:
    """Return the shared cache, or the in-memory fallback when host is empty."""
    if not host.strip():
        return InMemoryCache()
    return ValkeyCache(host=host.strip(), port=port, ttl_seconds=ttl_seconds)


def cache_status(configured: bool, reachable: bool = True) -> str:
    """Report cache readiness for the health endpoint."""
    if not configured:
        return "not_configured"
    return "ready" if reachable else "unavailable"
