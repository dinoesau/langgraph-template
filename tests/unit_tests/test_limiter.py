"""Unit tests for the in-memory auth rate limiter (brute-force stub)."""

import pytest

from src.core.limiter import RateLimiter


def test_allows_up_to_max_attempts() -> None:
    limiter = RateLimiter(max_attempts=2, window_seconds=60)
    limiter.check("1.2.3.4:/login")
    limiter.check("1.2.3.4:/login")
    with pytest.raises(PermissionError):
        limiter.check("1.2.3.4:/login")


def test_keys_are_isolated() -> None:
    limiter = RateLimiter(max_attempts=1, window_seconds=60)
    limiter.check("a:/login")
    limiter.check("b:/login")


def test_reset_clears_counts() -> None:
    limiter = RateLimiter(max_attempts=1, window_seconds=60)
    limiter.check("a:/login")
    limiter.reset()
    limiter.check("a:/login")
