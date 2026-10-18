"""Shared memory vocabulary: one namespace plus one recall formatter.

Both the loop and the memory adapter import from here so reads and writes
can never diverge. This module stays dependency-free on purpose.
"""

from typing import Any

MEMORY_NAMESPACE = "memories"
DEFAULT_TOP_K = 4


def memory_namespace(user_id: str) -> tuple[str, str]:
    """Return the per-user namespace so users never read each other rows."""
    return (MEMORY_NAMESPACE, user_id.strip())


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
