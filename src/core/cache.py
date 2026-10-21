"""Shared recall cache: Valkey front with local TTL fallback.

Keys come from ``core.memory`` so loop and adapter scope identically.
Only successful non-empty recall is cached by the caller; errors are
never cached. Sets warm both the shared front and the local map so a
Valkey flap cannot cause a miss storm.
"""

import logging
import threading
import time
from typing import Any

from core.memory import RecallCachePort

logger = logging.getLogger("cache")


class InMemoryTTLCache:
    """Single-instance TTL map guarded by one lock for both paths.

    One threading lock serves both paths because the sync path cannot hold
    an asyncio lock; sections do dict-only work with no I/O inside.
    """

    def __init__(self, max_entries: int = 1000) -> None:
        self._values: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()
        self._max_entries = max(1, max_entries)

    def __len__(self) -> int:
        with self._lock:
            return len(self._values)

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
            self._values.pop(key, None)
            self._values[key] = (value, time.monotonic() + max(1, ttl))
            while len(self._values) > self._max_entries:
                self._values.pop(next(iter(self._values)))

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

    def close_sync(self) -> None:
        """Close the sync client if one was created; safe to call twice."""
        client, self._sync_client = self._sync_client, None
        if client is None:
            return
        try:
            client.close()
        except Exception:  # noqa: BLE001 - shutdown must not raise
            logger.debug("valkey sync close failed")

    async def aclose(self) -> None:
        """Close the async client if one was created; safe to call twice."""
        client, self._async_client = self._async_client, None
        if client is None:
            return
        try:
            await client.aclose()
        except Exception:  # noqa: BLE001 - shutdown must not raise
            logger.debug("valkey async close failed")

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
        primary: RecallCachePort | None,
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

    def close_sync(self) -> None:
        """Close the shared front; safe with fallback-only fronts."""
        primary = self._primary
        closer = getattr(primary, "close_sync", None)
        if callable(closer):
            closer()

    async def aclose(self) -> None:
        """Close the shared front; safe with fallback-only fronts."""
        primary = self._primary
        closer = getattr(primary, "aclose", None)
        if callable(closer):
            await closer()


def build_cache(host: str) -> RecallCache:
    """Return a cache front with its own bounded fallback; empty host skips Valkey."""
    clean = host.strip()
    fallback = InMemoryTTLCache()
    if not clean:
        return RecallCache(primary=None, fallback=fallback)
    return RecallCache(primary=ValkeyCache(host=clean), fallback=fallback)
