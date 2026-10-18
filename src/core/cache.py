"""Shared recall cache: Valkey front with local TTL fallback.

Keys look like ``memory:{user_id}:{query_hash}`` so recall stays scoped
per requesting end user. Only successful non-empty recall is cached
by the caller; errors are never cached.
"""

import hashlib
import logging
import time
from typing import Any, Protocol

from core.config import get_settings

logger = logging.getLogger("cache")


def normalize_query(query: str) -> str:
    """Collapse whitespace and lowercase so similar asks share a key."""
    return " ".join(query.strip().lower().split())


def hash_query(query: str) -> str:
    """Return the 16 hex chars identifying one normalized query."""
    return hashlib.sha256(normalize_query(query).encode()).hexdigest()[:16]


def build_memory_key(user_id: str, query: str) -> str:
    """Return the scoped cache key for one user plus one query."""
    return f"memory:{user_id.strip()}:{hash_query(query)}"


class CacheBackend(Protocol):
    """Minimal async backend so Valkey and memory share one shape."""

    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str, ttl: int) -> None: ...


class InMemoryTTLCache:
    """Single-instance TTL map for local runs without Valkey."""

    def __init__(self) -> None:
        self._values: dict[str, tuple[str, float]] = {}

    async def get(self, key: str) -> str | None:
        found = self._values.get(key)
        if found is None:
            return None
        value, expires_at = found
        if expires_at <= time.monotonic():
            self._values.pop(key, None)
            return None
        return value

    async def set(self, key: str, value: str, ttl: int) -> None:
        self._values[key] = (value, time.monotonic() + max(1, ttl))

    def clear(self) -> None:
        """Drop every entry; tests use this for isolation."""
        self._values.clear()


class ValkeyCache:
    """Shared cache front speaking RESP to Valkey or Redis on 6379."""

    def __init__(self, host: str, port: int = 6379) -> None:
        self._host = host
        self._port = port
        self._client: Any = None

    async def _client_or_raise(self) -> Any:
        if self._client is not None:
            return self._client
        from redis.asyncio import Redis

        self._client = Redis(host=self._host, port=self._port, decode_responses=True)
        return self._client

    async def get(self, key: str) -> str | None:
        client = await self._client_or_raise()
        value = await client.get(key)
        return value if isinstance(value, str) else None

    async def set(self, key: str, value: str, ttl: int) -> None:
        client = await self._client_or_raise()
        await client.set(key, value, ex=max(1, ttl))


class RecallCache:
    """Try Valkey first, fall back to local memory on any failure."""

    def __init__(self, primary: CacheBackend | None, fallback: InMemoryTTLCache) -> None:
        self._primary = primary
        self._fallback = fallback

    async def get(self, key: str) -> str | None:
        if self._primary is not None:
            try:
                hit = await self._primary.get(key)
            except Exception:  # noqa: BLE001 - any backend miss uses fallback
                hit = None
            if hit is not None:
                return hit
        return await self._fallback.get(key)

    async def set(self, key: str, value: str, ttl: int) -> None:
        if self._primary is not None:
            try:
                await self._primary.set(key, value, ttl)
                return
            except Exception:  # noqa: BLE001 - fallback below keeps recall
                logger.debug("primary cache set failed, using fallback")
        await self._fallback.set(key, value, ttl)


_fallback = InMemoryTTLCache()
_instance: RecallCache | None = None


def build_cache(host: str) -> RecallCache:
    """Return a cache front; empty host means local fallback only."""
    clean = host.strip()
    if not clean:
        return RecallCache(primary=None, fallback=_fallback)
    return RecallCache(primary=ValkeyCache(host=clean), fallback=_fallback)


def get_cache() -> RecallCache:
    """Return the process cache front built once from settings."""
    global _instance
    if _instance is None:
        _instance = build_cache(get_settings().valkey_host)
    return _instance


def clear_cache_singleton() -> None:
    """Drop the cached front; tests use this after overriding settings."""
    global _instance
    _instance = None
    _fallback.clear()
