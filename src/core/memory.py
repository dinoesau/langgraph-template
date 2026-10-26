"""Shared memory vocabulary: one namespace plus one recall formatter.

Both the loop and the memory adapter import from here so reads and writes
can never diverge. This module stays dependency-free on purpose.
"""

import hashlib
from typing import Any, Protocol

MEMORY_NAMESPACE = "memories"
PASSAGES_NAMESPACE = "passages"
PASSAGES_TOPIC = "refund-policy"
DEFAULT_TOP_K = 4
PASSAGE_TOP_K = 3
MAX_USER_ID_CHARS = 128


class RecallCachePort(Protocol):
    """Cache port owned by the loop; adapters satisfy it structurally."""

    def get_sync(self, key: str) -> str | None: ...
    def set_sync(self, key: str, value: str, ttl: int) -> None: ...
    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str, ttl: int) -> None: ...


def memory_namespace(user_id: str) -> tuple[str, str]:
    """Return the per-user namespace so users never read each other rows."""
    return (MEMORY_NAMESPACE, user_id.strip())


def passage_namespace() -> tuple[str, str]:
    """Return the shared policy namespace; passages are global, not per-user."""
    return (PASSAGES_NAMESPACE, PASSAGES_TOPIC)


def valid_user_id(raw: str) -> str:
    """Return the stripped id, or empty when missing or overlong (fail closed)."""
    clean = " ".join(raw.strip().split())
    if not clean or len(raw.strip()) > MAX_USER_ID_CHARS:
        return ""
    return clean[:MAX_USER_ID_CHARS]


def normalize_query(query: str) -> str:
    """Collapse whitespace and lowercase so similar asks share a key."""
    return " ".join(query.strip().lower().split())


def hash_query(query: str) -> str:
    """Return the 16 hex chars identifying one normalized query."""
    return hashlib.sha256(normalize_query(query).encode()).hexdigest()[:16]


def build_memory_key(user_id: str, query: str) -> str:
    """Return the scoped cache key for one user plus one query."""
    return f"memory:{user_id.strip()}:{hash_query(query)}"


def recall_texts(hits: list[Any]) -> list[str]:
    """Extract the non-blank stored texts from raw store hits."""
    texts: list[str] = []
    for item in hits:
        value = item.value if hasattr(item, "value") else {}
        text = value.get("text", "") if isinstance(value, dict) else ""
        clean = str(text).strip()
        if clean:
            texts.append(clean)
    return texts


def format_recall(texts: list[str]) -> str:
    """Render texts as plain context text; empty means inject nothing."""
    cleaned = [text.strip() for text in texts if text.strip()]
    if not cleaned:
        return ""
    return "\n".join(f"- {text}" for text in cleaned)


def sanitize_tag_content(text: str) -> str:
    """Neutralize closing tags so recalled text stays data, never instructions."""
    return text.replace("</", "<\\/")
