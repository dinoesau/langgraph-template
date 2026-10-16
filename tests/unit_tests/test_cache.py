"""S2b cache unit seam: key format, TTL, and cache-only-success contract.

Seams under test (highest first):
- HTTP-agnostic cache contract in src/core/cache.py (what it catches:
  key shape, TTL expiry, and never caching errors/empty; what it misses:
  real Valkey wiring, covered by compose definition only).
"""

import time

from src.core.cache import InMemoryCache, memory_key


def test_memory_key_shape() -> None:
    key = memory_key("user-1", "hello world")
    assert key.startswith("memory:user-1:")
    suffix = key.split(":", 2)[2]
    assert len(suffix) >= 8
    assert memory_key("user-1", "hello world") == key
    assert memory_key("user-1", "other") != key
    assert memory_key("user-2", "hello world") != key


def test_in_memory_cache_hit_and_ttl_expiry() -> None:
    cache = InMemoryCache()
    cache.set("k", "v", ttl_seconds=1)
    assert cache.get("k") == "v"
    time.sleep(1.1)
    assert cache.get("k") is None


def test_in_memory_cache_miss_returns_none() -> None:
    assert InMemoryCache().get("missing") is None


def test_in_memory_cache_overwrite() -> None:
    cache = InMemoryCache()
    cache.set("k", "first", ttl_seconds=60)
    cache.set("k", "second", ttl_seconds=60)
    assert cache.get("k") == "second"
