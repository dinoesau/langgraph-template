"""S2b semantic memory: per-user recall with shared cache front plus pgvector.

Recall runs on the chat path behind the cache; writes run in background
after the reply with failures logged and ignored. Session scope stays on
``thread_id``; memory scope is always the requesting ``user_id`` so users
never see each other memories. Vector tables are owned here, never Alembic.
"""

import hashlib
import logging
from typing import Any, Protocol

from src.core.cache import InMemoryCache, ValkeyCache, memory_key

logger = logging.getLogger(__name__)

MEMORY_TABLE = "memory_embeddings"
EMBEDDING_DIM = 32


class MemoryStore(Protocol):
    """Sync vector-store boundary; pgvector or in-memory both satisfy it."""

    def search(self, user_id: str, query: str, limit: int) -> list[str]: ...
    def add(self, user_id: str, content: str) -> None: ...


def hash_embedding(text: str, dim: int = EMBEDDING_DIM) -> list[float]:
    """Deterministic offline embedding placeholder until a real embedder lands."""
    values: list[float] = []
    for index in range(dim):
        digest = hashlib.sha256(f"{text}:{index}".encode()).digest()
        unit: float = int.from_bytes(digest[:4], "big") / 2**32
        values.append(unit * 2.0 - 1.0)
    return values


class InMemoryMemoryStore:
    """Single-instance fallback store with per-user isolation, no I/O."""

    def __init__(self) -> None:
        self._items: dict[str, list[str]] = {}

    def seed(self, user_id: str, contents: list[str]) -> None:
        """Add fixed memories for tests and local work without embeddings."""
        self._items.setdefault(user_id, []).extend(contents)

    def search(self, user_id: str, query: str, limit: int) -> list[str]:
        items = self._items.get(user_id, [])
        if limit <= 0:
            return []
        return list(items[-limit:]) if items else []

    def add(self, user_id: str, content: str) -> None:
        if content.strip():
            self._items.setdefault(user_id, []).append(content)


class FakeMemoryStore(InMemoryMemoryStore):
    """Test double with failure injection and call counters, no network."""

    def __init__(self) -> None:
        super().__init__()
        self.search_calls = 0
        self._fail_search = False
        self._fail_add = False

    @property
    def memories(self) -> dict[str, list[str]]:
        return self._items

    def fail_next_search(self) -> None:
        """Make the next search raise, exercising the never-cache-errors path."""
        self._fail_search = True

    def fail_next_add(self) -> None:
        """Make the next add raise, exercising logged-and-ignored writes."""
        self._fail_add = True

    def search(self, user_id: str, query: str, limit: int) -> list[str]:
        self.search_calls += 1
        if self._fail_search:
            self._fail_search = False
            raise RuntimeError("fake search failure")
        return super().search(user_id, query, limit)

    def add(self, user_id: str, content: str) -> None:
        if self._fail_add:
            self._fail_add = False
            raise RuntimeError("fake add failure")
        super().add(user_id, content)


def ensure_memory_schema(sync_uri: str, table: str = MEMORY_TABLE) -> None:
    """Create the vector extension and memory table; safe to run on boot."""
    import psycopg

    with psycopg.connect(sync_uri, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cur.execute(
            f"""CREATE TABLE IF NOT EXISTS {table} (
                id SERIAL PRIMARY KEY,
                user_id TEXT NOT NULL,
                content TEXT NOT NULL,
                embedding vector({EMBEDDING_DIM}) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )"""
        )
        cur.execute(f"CREATE INDEX IF NOT EXISTS {table}_user_idx ON {table} (user_id)")


class PostgresMemoryStore:
    """pgvector-backed store; one row per memory, isolated by user_id."""

    def __init__(
        self, conn_string: str, table: str = MEMORY_TABLE, dim: int = EMBEDDING_DIM
    ) -> None:
        self._conn_string = conn_string
        self._table = table
        self._dim = dim

    @staticmethod
    def _literal(vector: list[float]) -> str:
        return "[" + ",".join(repr(float(value)) for value in vector) + "]"

    def search(self, user_id: str, query: str, limit: int) -> list[str]:
        import psycopg

        probe = self._literal(hash_embedding(query, self._dim))
        with psycopg.connect(self._conn_string) as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT content FROM {self._table} "
                "WHERE user_id = %s ORDER BY embedding <=> %s::vector LIMIT %s",
                (user_id, probe, limit),
            )
            rows = cur.fetchall()
        return [str(row[0]) for row in rows]

    def add(self, user_id: str, content: str) -> None:
        import psycopg

        if not content.strip():
            return
        vector = self._literal(hash_embedding(content, self._dim))
        with psycopg.connect(self._conn_string) as conn, conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {self._table} (user_id, content, embedding) "
                "VALUES (%s, %s, %s::vector)",
                (user_id, content, vector),
            )
            conn.commit()


CacheLike = InMemoryCache | ValkeyCache | dict[str, str]


class MemoryService:
    """Recall-through-cache plus background-safe remember for one user."""

    def __init__(
        self,
        store: MemoryStore,
        cache: CacheLike | None = None,
        ttl_seconds: int = 60,
        top_k: int = 3,
    ) -> None:
        self._store = store
        self._cache = cache
        self._ttl = ttl_seconds
        self._top_k = top_k

    def _cache_get(self, key: str) -> str | None:
        try:
            if self._cache is None:
                return None
            if isinstance(self._cache, dict):
                value = self._cache.get(key)
                return value if isinstance(value, str) and value else None
            found = self._cache.get(key)
            return found if isinstance(found, str) and found else None
        except Exception:
            logger.warning("memory cache get failed", exc_info=True)
            return None

    def _cache_set(self, key: str, value: str) -> None:
        try:
            if self._cache is None:
                return
            if isinstance(self._cache, dict):
                self._cache[key] = value
                return
            self._cache.set(key, value, ttl_seconds=self._ttl)
        except Exception:
            logger.warning("memory cache set failed", exc_info=True)

    def recall(self, user_id: str, query: str) -> str:
        """Return cached or freshly searched context for this user only."""
        if not query.strip():
            return ""
        key = memory_key(user_id, query)
        cached = self._cache_get(key)
        if cached:
            return cached
        try:
            results = self._store.search(user_id, query, self._top_k)
        except Exception:
            logger.warning("memory recall failed", exc_info=True)
            return ""
        texts = [item for item in results if isinstance(item, str) and item.strip()]
        if not texts:
            return ""
        joined = "\n".join(texts)
        self._cache_set(key, joined)
        return joined

    def remember(self, user_id: str, message: str, reply: str) -> None:
        """Persist one exchange; failures are logged and ignored by design."""
        content = f"User: {message}\nAssistant: {reply}".strip()
        if not content:
            return
        try:
            self._store.add(user_id, content)
        except Exception:
            logger.warning("memory write failed", exc_info=True)


def get_memory_service(app: Any) -> MemoryService | None:
    """Return the lifespan-provided memory service, else None for stateless."""
    candidate = getattr(getattr(app, "state", None), "memory_service", None)
    return candidate if isinstance(candidate, MemoryService) else None


def remember_in_background(
    service: MemoryService | None, user_id: str, message: str, reply: str
) -> None:
    """Background-task entry: never raises, so chat never fails on memory."""
    if service is None:
        return
    try:
        service.remember(user_id, message, reply)
    except Exception:
        logger.warning("memory background write failed", exc_info=True)
