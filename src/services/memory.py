"""Per-user semantic memory: extract plus background writer over BaseStore.

Nodes read recall through ``get_store`` with the shared namespace from
``core.memory``, so this adapter never leaks into ``src/agent``. Writes run
after the reply through the HTTP background writer; failures are logged
and ignored so chat stays correct when memory degrades. Matching stays
keyword-grade until a store index with real embeddings lands. Row keys are
content hashes so repeating a turn overwrites instead of growing the store.
"""

import hashlib
import logging

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.store.base import BaseStore

from core.memory import memory_namespace, normalize_query

__all__ = ["extract_memories", "persist_turn", "snapshot_messages"]

logger = logging.getLogger("memory")

MAX_MEMORY_CHARS = 500


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


def row_key(text: str) -> str:
    """Return the content hash so repeats overwrite instead of duplicating."""
    return hashlib.sha256(normalize_query(text).encode()).hexdigest()


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
            await store.aput(memory_namespace(clean_user), row_key(text), {"text": text})
    except Exception as exc:  # noqa: BLE001 - write miss keeps reply correct
        logger.warning("memory store degraded: %s", exc)


def snapshot_messages(human_text: str, reply_text: str | None) -> list[BaseMessage]:
    """Build one writer snapshot so invoke and stream persist equal shapes.

    A cancelled stream yields no reply, so the writer keeps the human turn
    instead of dropping the whole write.
    """
    messages: list[BaseMessage] = [HumanMessage(content=human_text)]
    if reply_text and reply_text.strip():
        messages.append(AIMessage(content=reply_text))
    return messages
