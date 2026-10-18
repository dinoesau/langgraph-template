"""Per-user semantic recall with cache front plus background writes.

The loop calls ``search`` through an injected hook so this adapter never
leaks into ``src/agent``. Writes run after the reply and failures are
logged and ignored so chat stays correct when memory degrades.
"""

import asyncio
import hashlib
import logging
from typing import Protocol

from langchain_core.messages import BaseMessage

from core.cache import build_memory_key, get_cache
from core.config import get_settings

logger = logging.getLogger("memory")

MEMORY_TABLE = "memory_vectors"
EMBEDDING_DIM = 64
DEFAULT_TOP_K = 4
MAX_MEMORY_CHARS = 500
MAX_STORED_PER_USER = 100


def embed_text(text: str) -> list[float]:
    """Deterministic hash embedding so unit tests need no network keys.

    A real embedder replaces this when LONG_TERM_MEMORY_EMBEDDER_MODEL
    is set; the Postgres schema already stores a vector column.
    """
    clean = " ".join(text.strip().lower().split())
    values: list[float] = []
    for width in range(EMBEDDING_DIM):
        digest = hashlib.sha256(f"{width}:{clean}".encode()).digest()
        values.append(digest[0] / 127.5 - 1.0)
    return values


def format_embedding(values: list[float]) -> str:
    """Render one embedding as a pgvector literal."""
    return "[" + ",".join(f"{part:.6f}" for part in values) + "]"


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


class VectorStore(Protocol):
    """Small port the loop never sees directly."""

    async def search(self, user_id: str, query: str, limit: int) -> list[str]: ...
    async def add(self, user_id: str, texts: list[str]) -> None: ...


class InMemoryVectorStore:
    """Keyword fallback for ephemeral runs and unit tests."""

    def __init__(self) -> None:
        self._rows: dict[str, list[str]] = {}

    async def search(self, user_id: str, query: str, limit: int) -> list[str]:
        rows = self._rows.get(user_id, [])
        if not rows or not query.strip():
            return []
        terms = set(" ".join(query.strip().lower().split()).split())
        scored: list[tuple[int, str]] = []
        for row in rows:
            words = set(row.lower().split())
            score = len(terms & words)
            if " ".join(sorted(terms)) in row.lower():
                score += 1
            if score > 0:
                scored.append((score, row))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [row for _, row in scored[: max(1, limit)]]

    async def add(self, user_id: str, texts: list[str]) -> None:
        cleaned = [text.strip() for text in texts if text.strip()]
        if not cleaned:
            return
        rows = self._rows.setdefault(user_id, [])
        rows.extend(cleaned)
        del rows[: max(0, len(rows) - MAX_STORED_PER_USER)]


class PostgresVectorStore:
    """pgvector backing; keyword fallback when the extension is missing."""

    def __init__(self, conninfo: str) -> None:
        self._conninfo = conninfo

    async def ensure_schema(self) -> None:
        import psycopg

        async with (
            await psycopg.AsyncConnection.connect(self._conninfo) as conn,
            conn.cursor() as cur,
        ):
            await cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            await cur.execute(
                f"""CREATE TABLE IF NOT EXISTS {MEMORY_TABLE} (
                    id SERIAL PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    embedding vector({EMBEDDING_DIM}),
                    created_at TIMESTAMPTZ DEFAULT now()
                )"""
            )
            await cur.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{MEMORY_TABLE}_user ON {MEMORY_TABLE} (user_id)"
            )

    async def search(self, user_id: str, query: str, limit: int) -> list[str]:
        import psycopg

        embedding = format_embedding(embed_text(query))
        async with (
            await psycopg.AsyncConnection.connect(self._conninfo) as conn,
            conn.cursor() as cur,
        ):
            try:
                await cur.execute(
                    f"SELECT content FROM {MEMORY_TABLE} "
                    "WHERE user_id = %s "
                    "ORDER BY embedding <=> %s::vector "
                    "LIMIT %s",
                    (user_id, embedding, max(1, limit)),
                )
                rows = await cur.fetchall()
                return [str(row[0]) for row in rows]
            except Exception:  # noqa: BLE001 - missing extension uses keyword path
                await conn.rollback()
                await cur.execute(
                    f"SELECT content FROM {MEMORY_TABLE} "
                    "WHERE user_id = %s AND content ILIKE %s "
                    "ORDER BY created_at DESC LIMIT %s",
                    (user_id, f"%{query.strip()}%", max(1, limit)),
                )
                rows = await cur.fetchall()
                return [str(row[0]) for row in rows]

    async def add(self, user_id: str, texts: list[str]) -> None:
        import psycopg

        cleaned = [text.strip()[:MAX_MEMORY_CHARS] for text in texts if text.strip()]
        if not cleaned:
            return
        async with (
            await psycopg.AsyncConnection.connect(self._conninfo) as conn,
            conn.cursor() as cur,
        ):
            for text in cleaned:
                await cur.execute(
                    f"INSERT INTO {MEMORY_TABLE} (user_id, content, embedding) "
                    "VALUES (%s, %s, %s::vector)",
                    (user_id, text, format_embedding(embed_text(text))),
                )


_memory_store: VectorStore | None = None


def build_store(postgres_uri: str) -> VectorStore:
    """Return pgvector when a URI is set, else the in-memory fallback."""
    if postgres_uri.strip():
        return PostgresVectorStore(conninfo=postgres_uri.strip())
    return InMemoryVectorStore()


def get_memory_store() -> VectorStore:
    """Return the process vector store built once from settings."""
    global _memory_store
    if _memory_store is None:
        _memory_store = build_store(get_settings().postgres_uri)
    return _memory_store


def clear_memory_singleton() -> None:
    """Drop the cached store; tests use this for isolation."""
    global _memory_store
    _memory_store = None


async def search(user_id: str, query: str, limit: int = DEFAULT_TOP_K) -> str:
    """Return per-user recall as plain text; empty means inject nothing."""
    clean_user = user_id.strip()
    if not clean_user or not query.strip():
        return ""
    key = build_memory_key(clean_user, query)
    try:
        cached = await get_cache().get(key)
    except Exception as exc:  # noqa: BLE001 - cache miss degrades to store
        logger.warning("memory cache get degraded: %s", exc)
        cached = None
    if cached:
        return cached
    try:
        hits = await get_memory_store().search(clean_user, query, limit)
    except Exception as exc:  # noqa: BLE001 - vector miss degrades to empty
        logger.warning("memory search degraded: %s", exc)
        return ""
    if not hits:
        return ""
    recall = "\n".join(f"- {hit}" for hit in hits)
    try:
        await get_cache().set(key, recall, get_settings().cache_ttl_seconds)
    except Exception as exc:  # noqa: BLE001 - recall stands without cache
        logger.warning("memory cache set degraded: %s", exc)
    return recall


async def store_turn(user_id: str, messages: list[BaseMessage]) -> None:
    """Extract and save memories; failures are logged and ignored."""
    clean_user = user_id.strip()
    if not clean_user:
        return
    texts = extract_memories(messages)
    if not texts:
        return
    try:
        await get_memory_store().add(clean_user, texts)
    except Exception as exc:  # noqa: BLE001 - write miss keeps reply correct
        logger.warning("memory store degraded: %s", exc)


def schedule_store(user_id: str, messages: list[BaseMessage]) -> None:
    """Fire-and-forget write after the reply; never slows the chat path."""
    clean_user = user_id.strip()
    if not clean_user:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    snapshot = list(messages)

    async def _run() -> None:
        await store_turn(clean_user, snapshot)

    task = loop.create_task(_run())

    def _log_failure(done: asyncio.Task[None]) -> None:
        if done.cancelled():
            return
        exc = done.exception()
        if exc is not None:
            logger.warning("memory background write degraded: %s", exc)

    task.add_done_callback(_log_failure)


async def ensure_memory_schema() -> None:
    """Pre-warm the vector table at startup; degrade, never crash boot."""
    uri = get_settings().postgres_uri.strip()
    if not uri:
        return
    store = get_memory_store()
    if isinstance(store, PostgresVectorStore):
        try:
            await store.ensure_schema()
        except Exception as exc:  # noqa: BLE001 - boot stands without vectors
            logger.warning("memory schema degraded: %s", exc)
