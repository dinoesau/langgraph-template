"""Shared recall cache: Valkey front with local TTL fallback.

Keys look like ``memory:{user_id}:{query_hash}`` so recall stays scoped
per requesting end user. Only successful non-empty recall is cached
by the caller; errors are never cached. Sets warm both the shared front
and the local map so a Valkey flap cannot cause a miss storm.
"""

import hashlib
import logging
import threading
import time
from typing import Any, Protocol

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


class SyncCacheBackend(Protocol):
    """Synchronous backend shape for the sync loop path."""

    def get_sync(self, key: str) -> str | None: ...
    def set_sync(self, key: str, value: str, ttl: int) -> None: ...


class AsyncCacheBackend(Protocol):
    """Asynchronous backend shape for the async loop path."""

    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str, ttl: int) -> None: ...


class DualCacheBackend(SyncCacheBackend, AsyncCacheBackend, Protocol):
    """One backend serving both loop paths, like the Valkey front."""


class InMemoryTTLCache:
    """Single-instance TTL map guarded by one lock for both paths."""

    def __init__(self) -> None:
        self._values: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()

    def get_sync(self, key: str) -> str | None:
        with self._lock:
            found = self._values.get(key)
            if found is None:
                return None
            value, expires_at = found
            if expires_at <= time.monotonic():
                self._values.pop(key, None)
                return None
            return value

    def set_sync(self, key: str, value: str, ttl: int) -> None:
        with self._lock:
            self._values[key] = (value, time.monotonic() + max(1, ttl))

    async def get(self, key: str) -> str | None:
        return self.get_sync(key)

    async def set(self, key: str, value: str, ttl: int) -> None:
        self.set_sync(key, value, ttl)

    def clear(self) -> None:
        """Drop every entry; tests use this for isolation."""
        with self._lock:
            self._values.clear()


class ValkeyCache:
    """Shared cache front speaking RESP to Valkey or Redis on 6379."""

    def __init__(self, host: str, port: int = 6379) -> None:
        self._host = host
        self._port = port
        self._async_client: Any = None
        self._sync_client: Any = None

    def _sync_client_or_raise(self) -> Any:
        if self._sync_client is not None:
            return self._sync_client
        from redis import Redis

        self._sync_client = Redis(host=self._host, port=self._port, decode_responses=True)
        return self._sync_client

    async def _async_client_or_raise(self) -> Any:
        if self._async_client is not None:
            return self._async_client
        from redis.asyncio import Redis

        self._async_client = Redis(host=self._host, port=self._port, decode_responses=True)
        return self._async_client

    def get_sync(self, key: str) -> str | None:
        value = self._sync_client_or_raise().get(key)
        return value if isinstance(value, str) else None

    def set_sync(self, key: str, value: str, ttl: int) -> None:
        self._sync_client_or_raise().set(key, value, ex=max(1, ttl))

    async def get(self, key: str) -> str | None:
        client = await self._async_client_or_raise()
        value = await client.get(key)
        return value if isinstance(value, str) else None

    async def set(self, key: str, value: str, ttl: int) -> None:
        client = await self._async_client_or_raise()
        await client.set(key, value, ex=max(1, ttl))


class RecallCache:
    """Try Valkey first, fall back to local memory on any failure."""

    def __init__(
        self,
        primary: DualCacheBackend | None,
        fallback: InMemoryTTLCache,
    ) -> None:
        self._primary = primary
        self._fallback = fallback

    def get_sync(self, key: str) -> str | None:
        if self._primary is not None:
            try:
                hit = self._primary.get_sync(key)
            except Exception:  # noqa: BLE001 - any backend miss uses fallback
                hit = None
            if hit is not None:
                return hit
        return self._fallback.get_sync(key)

    async def get(self, key: str) -> str | None:
        if self._primary is not None:
            try:
                hit = await self._primary.get(key)
            except Exception:  # noqa: BLE001 - any backend miss uses fallback
                hit = None
            if hit is not None:
                return hit
        return await self._fallback.get(key)

    def set_sync(self, key: str, value: str, ttl: int) -> None:
        if self._primary is not None:
            try:
                self._primary.set_sync(key, value, ttl)
            except Exception:  # noqa: BLE001 - fallback below keeps recall
                logger.debug("primary cache set failed, using fallback")
        self._fallback.set_sync(key, value, ttl)

    async def set(self, key: str, value: str, ttl: int) -> None:
        if self._primary is not None:
            try:
                await self._primary.set(key, value, ttl)
            except Exception:  # noqa: BLE001 - fallback below keeps recall
                logger.debug("primary cache set failed, using fallback")
        await self._fallback.set(key, value, ttl)


def build_cache(host: str) -> RecallCache:
    """Return a cache front with its own fallback; empty host skips Valkey."""
    clean = host.strip()
    if not clean:
        return RecallCache(primary=None, fallback=InMemoryTTLCache())
    return RecallCache(primary=ValkeyCache(host=clean), fallback=InMemoryTTLCache())
