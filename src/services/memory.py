"""Per-user semantic memory: extract plus BaseStore wiring plus the writer.

Nodes read recall through ``get_store`` with per-user namespaces, so this
adapter never leaks into ``src/agent``. Writes run after the reply through
the HTTP background writer; failures are logged and ignored so chat stays
correct when memory degrades. Matching stays keyword-grade until a store
index with real embeddings lands.
"""

import logging
import uuid

from langchain_core.messages import BaseMessage
from langgraph.store.base import BaseStore

logger = logging.getLogger("memory")

MEMORY_NAMESPACE = "memories"
DEFAULT_TOP_K = 4
MAX_MEMORY_CHARS = 500


def memory_namespace(user_id: str) -> tuple[str, str]:
    """Return the per-user namespace so users never read each other rows."""
    return (MEMORY_NAMESPACE, user_id.strip())


def extract_memories(messages: list[BaseMessage]) -> list[str]:
    """Heuristic extraction: keep the last non-blank human and AI texts."""
    kept: list[str] = []
    for message in reversed(messages):
        content = message.content
        if not isinstance(content, str):
            continue
        clean = " ".join(content.strip().split())
        if not clean:
            continue
        if clean.lower().startswith("echo:"):
            clean = clean[len("echo:") :].strip()
            if not clean:
                continue
        kept.append(clean[:MAX_MEMORY_CHARS])
        if len(kept) >= 2:
            break
    return list(reversed(kept))


def format_recall(texts: list[str]) -> str:
    """Render hits as plain context text; empty means inject nothing."""
    cleaned = [text.strip() for text in texts if text.strip()]
    if not cleaned:
        return ""
    return "\n".join(f"- {text}" for text in cleaned)


async def persist_turn(store: BaseStore, user_id: str, messages: list[BaseMessage]) -> None:
    """Extract and save memories; failures are logged and ignored."""
    clean_user = user_id.strip()
    if not clean_user:
        return
    texts = extract_memories(messages)
    if not texts:
        return
    try:
        for text in texts:
            await store.aput(memory_namespace(clean_user), uuid.uuid4().hex, {"text": text})
    except Exception as exc:  # noqa: BLE001 - write miss keeps reply correct
        logger.warning("memory store degraded: %s", exc)
