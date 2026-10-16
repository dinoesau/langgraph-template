"""S2b integration: real pgvector proves per-user memory isolation plus cache front.

Runs only with Docker available. Uses an ephemeral pgvector container on a
random host port plus a dedicated test database, never the development
database. Memory tables are owned by the memory layer, never Alembic.
"""

import shutil
import socket
import subprocess
import time
import uuid
from collections.abc import Iterator

import psycopg
import pytest

from src.core.cache import memory_key
from src.services.memory import (
    EMBEDDING_DIM,
    MEMORY_TABLE,
    MemoryService,
    PostgresMemoryStore,
    ensure_memory_schema,
)

IMAGE = "pgvector/pgvector:0.8.0-pg15"
TEST_DB = "test_s2b_memory"


def _docker_ok() -> bool:
    if shutil.which("docker") is None:
        return False
    probe = subprocess.run(["docker", "info"], check=False, capture_output=True, timeout=30)
    return probe.returncode == 0


needs_docker = pytest.mark.skipif(not _docker_ok(), reason="docker is not available")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_ready(port: int) -> None:
    deadline = time.time() + 120
    last: Exception | None = None
    while time.time() < deadline:
        try:
            with psycopg.connect(
                f"postgresql://postgres:postgres@127.0.0.1:{port}/postgres",
                connect_timeout=2,
            ):
                return
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.5)
    raise AssertionError(f"pgvector never became ready: {last}")


@pytest.fixture(scope="module")
def pg_uri() -> Iterator[str]:
    name = f"pg-s2b-{uuid.uuid4().hex[:12]}"
    port = _free_port()
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            name,
            "-e",
            "POSTGRES_PASSWORD=postgres",
            "-p",
            f"127.0.0.1:{port}:5432",
            IMAGE,
        ],
        check=True,
        capture_output=True,
        timeout=180,
    )
    try:
        _wait_ready(port)
        with (
            psycopg.connect(
                f"postgresql://postgres:postgres@127.0.0.1:{port}/postgres", autocommit=True
            ) as conn,
            conn.cursor() as cur,
        ):
            cur.execute(f'CREATE DATABASE "{TEST_DB}"')
        uri = f"postgresql://postgres:postgres@127.0.0.1:{port}/{TEST_DB}"
        ensure_memory_schema(uri)
        yield uri
    finally:
        subprocess.run(["docker", "stop", name], check=False, capture_output=True, timeout=60)


@needs_docker
def test_vector_extension_and_table_exist(pg_uri: str) -> None:
    with psycopg.connect(pg_uri) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
        assert cur.fetchone() is not None
        cur.execute(
            "SELECT column_name, udt_name FROM information_schema.columns "
            f"WHERE table_name = '{MEMORY_TABLE}'"
        )
        columns = {row[0]: row[1] for row in cur.fetchall()}
    assert columns.get("user_id") == "text"
    assert columns.get("content") == "text"
    assert columns.get("embedding") == "vector"
    # Memory schema never creates loop or app tables.
    with psycopg.connect(pg_uri) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        )
        tables = {row[0] for row in cur.fetchall()}
    assert MEMORY_TABLE in tables
    assert tables.isdisjoint({"checkpoints", "checkpoint_blobs", "checkpoint_writes"})


@needs_docker
def test_postgres_store_isolates_users(pg_uri: str) -> None:
    alice = f"alice-{uuid.uuid4().hex[:8]}"
    bob = f"bob-{uuid.uuid4().hex[:8]}"
    store = PostgresMemoryStore(pg_uri)
    store.add(alice, "alice prefers morning slots")
    store.add(bob, "bob prefers evening slots")
    alice_hits = store.search(alice, "when should I book", 3)
    assert any("morning slots" in hit for hit in alice_hits)
    assert all("evening slots" not in hit for hit in alice_hits)
    bob_hits = store.search(bob, "when should I book", 3)
    assert any("evening slots" in hit for hit in bob_hits)
    assert all("morning slots" not in hit for hit in bob_hits)


@needs_docker
def test_recall_caches_non_empty_on_real_store(pg_uri: str) -> None:
    user = f"carol-{uuid.uuid4().hex[:8]}"
    store = PostgresMemoryStore(pg_uri)
    store.add(user, "carol likes refund policy answers with citations")
    cache: dict[str, str] = {}
    service = MemoryService(store=store, cache=cache, ttl_seconds=60, top_k=3)
    first = service.recall(user, "refund policy")
    assert "carol likes refund" in first
    expected_key = memory_key(user, "refund policy")
    assert cache.get(expected_key) == first
    # Second recall serves the cached copy even after the row is gone.
    with psycopg.connect(pg_uri, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(f"DELETE FROM {MEMORY_TABLE} WHERE user_id = %s", (user,))
    assert service.recall(user, "refund policy") == first


@needs_docker
def test_embedding_dimension_matches_column(pg_uri: str) -> None:
    with psycopg.connect(pg_uri) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT atttypmod FROM pg_attribute WHERE attrelid = %s::regclass AND attname = 'embedding'",
            (MEMORY_TABLE,),
        )
        row = cur.fetchone()
    assert row is not None
    assert int(row[0]) == EMBEDDING_DIM
