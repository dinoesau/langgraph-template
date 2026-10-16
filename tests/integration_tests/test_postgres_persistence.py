"""S2a integration: real Postgres proves resume, restart, and migration boundary.

Runs only with Docker available. Uses an ephemeral container on a random host
port plus a dedicated test database, never the development database.
"""

import asyncio
import shutil
import socket
import subprocess
import time
import uuid
from collections.abc import Iterator

import psycopg
import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import create_engine, inspect

from alembic import command
from src.agent.graph import make_the_agent
from src.api.app import create_app
from src.core.config import get_settings

IMAGE = "postgres:15-alpine"
TEST_DB = "test_s2a"


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
    deadline = time.time() + 60
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
    raise AssertionError(f"postgres never became ready: {last}")


@pytest.fixture(scope="module")
def pg_uris() -> Iterator[dict[str, str]]:
    name = f"pg-s2a-{uuid.uuid4().hex[:12]}"
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
        timeout=120,
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
        sync = f"postgresql+psycopg://postgres:postgres@127.0.0.1:{port}/{TEST_DB}"
        cfg = Config("alembic.ini")
        cfg.set_main_option("sqlalchemy.url", sync)
        command.upgrade(cfg, "head")
        yield {
            "sync": sync,
            "async": f"postgresql://postgres:postgres@127.0.0.1:{port}/{TEST_DB}",
        }
    finally:
        subprocess.run(["docker", "stop", name], check=False, capture_output=True, timeout=60)


def _contents(result: object) -> list[str]:
    assert isinstance(result, dict)
    return [str(message.content) for message in result["messages"]]


@needs_docker
def test_migration_boundary_on_real_postgres(pg_uris: dict[str, str]) -> None:
    tables = set(inspect(create_engine(pg_uris["sync"])).get_table_names())
    assert {"users", "sessions", "alembic_version"} <= tables
    assert tables.isdisjoint({"checkpoints", "checkpoint_blobs", "checkpoint_writes"})


def _aconfig(thread_id: str) -> RunnableConfig:
    return {"configurable": {"thread_id": thread_id}}


@needs_docker
def test_thread_resumes_across_saver_restart(pg_uris: dict[str, str]) -> None:
    async def _run() -> None:
        thread = f"restart-{uuid.uuid4().hex}"
        config = _aconfig(thread)
        async with AsyncPostgresSaver.from_conn_string(pg_uris["async"]) as saver:
            await saver.setup()
            agent = make_the_agent(checkpointer=saver)
            await agent.ainvoke({"messages": [HumanMessage(content="first")]}, config=config)
            second = await agent.ainvoke(
                {"messages": [HumanMessage(content="second")]}, config=config
            )
            assert _contents(second) == ["first", "first", "second", "second"]
        async with AsyncPostgresSaver.from_conn_string(pg_uris["async"]) as fresh:
            await fresh.setup()
            resumed = await make_the_agent(checkpointer=fresh).ainvoke(
                {"messages": [HumanMessage(content="third")]}, config=config
            )
            assert _contents(resumed) == [
                "first",
                "first",
                "second",
                "second",
                "third",
                "third",
            ]

    asyncio.run(_run())


@needs_docker
def test_http_thread_resumes_across_app_restart(
    pg_uris: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("POSTGRES_URI", pg_uris["async"])
    get_settings.cache_clear()
    try:

        def _auth_headers(app_client: TestClient) -> tuple[dict[str, str], str]:
            email = f"http-{uuid.uuid4().hex}@example.com"
            reg = app_client.post(
                "/api/v1/auth/register",
                json={"email": email, "password": "ChangeMe123!", "username": "http"},
            )
            assert reg.status_code == 200
            login = app_client.post(
                "/api/v1/auth/login",
                data={"username": email, "password": "ChangeMe123!", "grant_type": "password"},
            )
            assert login.status_code == 200
            user_token = str(login.json()["access_token"])
            created = app_client.post(
                "/api/v1/auth/session",
                json={},
                headers={"Authorization": f"Bearer {user_token}"},
            )
            assert created.status_code == 200
            session_token = str(created.json()["token"])
            session_id = str(created.json()["session_id"])
            return {"Authorization": f"Bearer {session_token}"}, session_id

        async def _message_count(thread: str) -> int:
            async with AsyncPostgresSaver.from_conn_string(pg_uris["async"]) as saver:
                state = await saver.aget_tuple(_aconfig(thread))
                assert state is not None
                values = state.checkpoint["channel_values"]
                return len(values["messages"])

        with TestClient(create_app()) as first:
            assert first.get("/health").json()["dependencies"]["store"] == "ready"
            headers, thread = _auth_headers(first)
            assert first.post(
                "/api/v1/chat", json={"message": "hello"}, headers=headers
            ).json() == {"reply": "hello", "thread_id": thread}
            assert first.post(
                "/api/v1/chat", json={"message": "again"}, headers=headers
            ).json() == {"reply": "again", "thread_id": thread}
        with TestClient(create_app()) as second:
            assert second.get("/health").json()["dependencies"]["store"] == "ready"
            reply = second.post("/api/v1/chat", json={"message": "third"}, headers=headers)
            assert reply.json() == {"reply": "third", "thread_id": thread}
        assert asyncio.run(_message_count(thread)) == 6
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
