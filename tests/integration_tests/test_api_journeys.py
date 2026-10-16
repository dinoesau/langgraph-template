"""S7a API journeys: seeded register plus login plus session plus chat plus history.

Full-stack equivalent over HTTP with session tokens: an ephemeral pgvector
Postgres (or a TEST_POSTGRES_URI override) plus TestClient lifespan wiring the
checkpointer, memory, and cache. Each test seeds predictable users with fresh
session threads and the module stops the container afterwards, so dev data is
never touched. The loop answers through a fake echo, keeping the suite
network-free apart from the ephemeral store. No browser automation.
"""

import asyncio
import os
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
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from alembic import command
from src.api import deps
from src.api.app import create_app
from src.core.config import get_settings
from src.core.limiter import auth_rate_limiter
from src.services.llm.service import llm_service

PASSWORD = "ChangeMe123!"
IMAGE = "pgvector/pgvector:0.8.0-pg15"
TEST_DB = "test_s7a_journeys"


@pytest.fixture(autouse=True)
def _fake_llm_echo(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep journeys hermetic: the loop answers through a fake echo."""

    async def _echo(messages: object, **kwargs: object) -> AIMessage:
        text = ""
        assert isinstance(messages, list)
        for message in reversed(messages):
            if isinstance(message, HumanMessage):
                text = message.content if isinstance(message.content, str) else ""
                break
        return AIMessage(content=text)

    monkeypatch.setattr(llm_service, "call", _echo)


def _docker_ok() -> bool:
    if shutil.which("docker") is None:
        return False
    probe = subprocess.run(["docker", "info"], check=False, capture_output=True, timeout=30)
    return probe.returncode == 0


def _external_uri() -> str | None:
    candidate = os.environ.get("TEST_POSTGRES_URI", "").strip()
    return candidate or None


needs_store = pytest.mark.skipif(
    not _docker_ok() and not _external_uri(), reason="docker and TEST_POSTGRES_URI unavailable"
)


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
    raise AssertionError(f"journey postgres never became ready: {last}")


def _split_uris(uri: str) -> dict[str, str]:
    if uri.startswith("postgresql+psycopg://"):
        sync = uri
        async_uri = uri.replace("postgresql+psycopg://", "postgresql://", 1)
    else:
        async_uri = uri
        sync = (
            uri.replace("postgresql://", "postgresql+psycopg://", 1)
            if uri.startswith("postgresql://")
            else uri
        )
    return {"sync": sync, "async": async_uri}


def _migrate(sync_uri: str) -> None:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", sync_uri)
    command.upgrade(cfg, "head")


@pytest.fixture(scope="module")
def pg_journey_uris() -> Iterator[dict[str, str]]:
    """Ephemeral store for journeys: TEST_POSTGRES_URI wins, else a container."""
    external = _external_uri()
    if external is not None:
        uris = _split_uris(external)
        _migrate(uris["sync"])
        yield uris
        return
    name = f"pg-s7a-{uuid.uuid4().hex[:12]}"
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
        uris = {
            "sync": f"postgresql+psycopg://postgres:postgres@127.0.0.1:{port}/{TEST_DB}",
            "async": f"postgresql://postgres:postgres@127.0.0.1:{port}/{TEST_DB}",
        }
        _migrate(uris["sync"])
        yield uris
    finally:
        subprocess.run(["docker", "stop", name], check=False, capture_output=True, timeout=60)


def _reset_auth_engine() -> None:
    engine = deps._default_engine
    if engine is not None:
        engine.dispose()
        deps._default_engine = None


@pytest.fixture()
def journey_client(
    pg_journey_uris: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    """TestClient bound to the ephemeral store with predictable teardown."""
    monkeypatch.setenv("POSTGRES_URI", pg_journey_uris["async"])
    monkeypatch.setenv("VALKEY_HOST", "")
    get_settings.cache_clear()
    _reset_auth_engine()
    auth_rate_limiter.reset()
    app = create_app()
    with TestClient(app) as client:
        yield client
    auth_rate_limiter.reset()
    monkeypatch.undo()
    get_settings.cache_clear()
    _reset_auth_engine()


def _unique_email(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}@example.com"


def _register(client: TestClient, email: str) -> None:
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "username": email.split("@")[0]},
    )
    assert response.status_code == 200


def _login(client: TestClient, email: str) -> str:
    response = client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": PASSWORD, "grant_type": "password"},
    )
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, dict)
    return str(body["access_token"])


def _open_session(client: TestClient, user_token: str) -> tuple[str, str]:
    response = client.post(
        "/api/v1/auth/session",
        json={},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, dict)
    return str(body["session_id"]), str(body["token"])


def _chat(client: TestClient, session_token: str, message: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/chat",
        json={"message": message},
        headers={"Authorization": f"Bearer {session_token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, dict)
    return {"reply": str(body["reply"]), "thread_id": str(body["thread_id"])}


def _aconfig(thread_id: str) -> RunnableConfig:
    return {"configurable": {"thread_id": thread_id}}


def _checkpoint_messages(async_uri: str, thread_id: str) -> list[str]:
    async def _run() -> list[str]:
        async with AsyncPostgresSaver.from_conn_string(async_uri) as saver:
            state = await saver.aget_tuple(_aconfig(thread_id))
            assert state is not None
            values = state.checkpoint["channel_values"]
            messages = values["messages"]
            return [str(message.content) for message in messages]

    return asyncio.run(_run())


@needs_store
def test_auth_lifecycle_journey_register_login_session_chat_history(
    journey_client: TestClient, pg_journey_uris: dict[str, str]
) -> None:
    """Seeded auth lifecycle: register, login, session, two chats, history visible."""
    email = _unique_email("ada")
    _register(journey_client, email)
    user_token = _login(journey_client, email)
    user_headers = {"Authorization": f"Bearer {user_token}"}

    session_id, session_token = _open_session(journey_client, user_token)
    headers = {"Authorization": f"Bearer {session_token}"}

    first = _chat(journey_client, session_token, "hello journey")
    assert first == {"reply": "hello journey", "thread_id": session_id}
    second = _chat(journey_client, session_token, "second turn")
    assert second["thread_id"] == session_id
    assert "second turn" in second["reply"]

    history = _checkpoint_messages(pg_journey_uris["async"], session_id)
    assert len(history) == 4
    assert history[0] == "hello journey"
    assert history[1] == "hello journey"
    assert "second turn" in history[2]
    assert history[3] == second["reply"]
    assert "second turn" in history[3]

    renamed = journey_client.patch(
        f"/api/v1/auth/session/{session_id}/name", json={"name": "research"}, headers=headers
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "research"

    listed = journey_client.get("/api/v1/auth/sessions", headers=user_headers)
    assert listed.status_code == 200
    assert [item["session_id"] for item in listed.json()["sessions"]] == [session_id]

    deleted = journey_client.delete(f"/api/v1/auth/session/{session_id}", headers=headers)
    assert deleted.status_code == 200
    remaining = journey_client.get("/api/v1/auth/sessions", headers=user_headers)
    assert remaining.json() == {"sessions": []}


@needs_store
def test_chat_journey_resumes_thread_across_restart(
    journey_client: TestClient, pg_journey_uris: dict[str, str]
) -> None:
    """Two turns persist, then a fresh app instance resumes the same thread."""
    email = _unique_email("resume")
    _register(journey_client, email)
    user_token = _login(journey_client, email)
    session_id, session_token = _open_session(journey_client, user_token)

    first = _chat(journey_client, session_token, "first hello")
    assert first["thread_id"] == session_id
    second = _chat(journey_client, session_token, "again please")
    assert second["thread_id"] == session_id
    assert "again please" in second["reply"]

    before = _checkpoint_messages(pg_journey_uris["async"], session_id)
    assert len(before) == 4

    with TestClient(create_app()) as restarted:
        assert restarted.get("/health").json()["dependencies"]["store"] == "ready"
        third_body = _chat(restarted, session_token, "third time")
        assert third_body["thread_id"] == session_id
        assert "third time" in third_body["reply"]

    after = _checkpoint_messages(pg_journey_uris["async"], session_id)
    assert len(after) == 6
    assert "third time" in after[-2]
    assert after[-1] == third_body["reply"]


@needs_store
def test_history_isolation_across_users(
    journey_client: TestClient, pg_journey_uris: dict[str, str]
) -> None:
    """Two users chat on fresh threads; neither history nor listing leaks."""
    ada_email = _unique_email("ada")
    bob_email = _unique_email("bob")
    _register(journey_client, ada_email)
    _register(journey_client, bob_email)
    ada_user = _login(journey_client, ada_email)
    bob_user = _login(journey_client, bob_email)

    ada_id, ada_session = _open_session(journey_client, ada_user)
    bob_id, bob_session = _open_session(journey_client, bob_user)
    assert ada_id != bob_id

    ada_first = _chat(journey_client, ada_session, "ada morning note")
    bob_first = _chat(journey_client, bob_session, "bob evening note")
    assert ada_first["thread_id"] == ada_id
    assert bob_first["thread_id"] == bob_id
    _chat(journey_client, ada_session, "ada second note")

    ada_history = _checkpoint_messages(pg_journey_uris["async"], ada_id)
    bob_history = _checkpoint_messages(pg_journey_uris["async"], bob_id)
    assert any("ada morning note" in item for item in ada_history)
    assert all("bob evening note" not in item for item in ada_history)
    assert any("bob evening note" in item for item in bob_history)
    assert all("ada morning note" not in item for item in bob_history)

    ada_list = journey_client.get(
        "/api/v1/auth/sessions", headers={"Authorization": f"Bearer {ada_user}"}
    )
    bob_list = journey_client.get(
        "/api/v1/auth/sessions", headers={"Authorization": f"Bearer {bob_user}"}
    )
    assert [item["session_id"] for item in ada_list.json()["sessions"]] == [ada_id]
    assert [item["session_id"] for item in bob_list.json()["sessions"]] == [bob_id]

    hijack = journey_client.patch(
        f"/api/v1/auth/session/{ada_id}/name",
        json={"name": "hijack"},
        headers={"Authorization": f"Bearer {bob_session}"},
    )
    assert hijack.status_code == 403
    cross_chat = journey_client.post(
        "/api/v1/chat",
        json={"message": "hi", "thread_id": ada_id},
        headers={"Authorization": f"Bearer {bob_session}"},
    )
    assert cross_chat.status_code == 403


@needs_store
def test_journey_rejects_unauthorized_and_forbidden(journey_client: TestClient) -> None:
    """Missing tokens are 401; wrong scope or foreign thread is 403."""
    assert journey_client.post("/api/v1/chat", json={"message": "hi"}).status_code == 401
    assert journey_client.get("/api/v1/auth/sessions").status_code == 401
    assert journey_client.post("/api/v1/auth/session", json={}).status_code == 401

    bad = journey_client.post(
        "/api/v1/chat",
        json={"message": "hi"},
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert bad.status_code == 401
    assert bad.json()["error"]["code"]
    assert bad.headers["X-Request-ID"]

    email = _unique_email("forbidden")
    _register(journey_client, email)
    user_token = _login(journey_client, email)
    user_headers = {"Authorization": f"Bearer {user_token}"}
    session_id, session_token = _open_session(journey_client, user_token)
    session_headers = {"Authorization": f"Bearer {session_token}"}

    user_on_chat = journey_client.post("/api/v1/chat", json={"message": "hi"}, headers=user_headers)
    assert user_on_chat.status_code == 403
    session_on_list = journey_client.get("/api/v1/auth/sessions", headers=session_headers)
    assert session_on_list.status_code == 403
    session_on_create = journey_client.post(
        "/api/v1/auth/session", json={}, headers=session_headers
    )
    assert session_on_create.status_code == 403

    mismatch = journey_client.post(
        "/api/v1/chat",
        json={"message": "hi", "thread_id": "some-other-thread"},
        headers=session_headers,
    )
    assert mismatch.status_code == 403

    bad_login = journey_client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": "WrongPass123!", "grant_type": "password"},
    )
    assert bad_login.status_code == 401

    assert session_id and session_token


@needs_store
def test_journey_rejects_not_found_and_validation(journey_client: TestClient) -> None:
    """Gone sessions are 404; malformed payloads are 422 with the error envelope."""
    email = _unique_email("gone")
    _register(journey_client, email)
    user_token = _login(journey_client, email)
    session_id, session_token = _open_session(journey_client, user_token)
    headers = {"Authorization": f"Bearer {session_token}"}

    deleted = journey_client.delete(f"/api/v1/auth/session/{session_id}", headers=headers)
    assert deleted.status_code == 200
    renamed_gone = journey_client.patch(
        f"/api/v1/auth/session/{session_id}/name", json={"name": "late"}, headers=headers
    )
    assert renamed_gone.status_code == 404
    deleted_again = journey_client.delete(f"/api/v1/auth/session/{session_id}", headers=headers)
    assert deleted_again.status_code == 404

    unknown = journey_client.get("/does-not-exist")
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "not_found"

    fresh_id, fresh_token = _open_session(journey_client, user_token)
    fresh_headers = {"Authorization": f"Bearer {fresh_token}"}
    assert fresh_id != session_id

    empty_chat = journey_client.post("/api/v1/chat", json={"message": ""}, headers=fresh_headers)
    assert empty_chat.status_code == 422
    assert empty_chat.json()["error"]["code"] == "validation_error"
    assert empty_chat.json()["request_id"] == empty_chat.headers["X-Request-ID"]

    missing_field = journey_client.post("/api/v1/chat", json={}, headers=fresh_headers)
    assert missing_field.status_code == 422

    weak_password = journey_client.post(
        "/api/v1/auth/register",
        json={"email": _unique_email("weak"), "password": "weak", "username": "weak"},
    )
    assert weak_password.status_code == 422

    bad_grant = journey_client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": PASSWORD, "grant_type": "client_credentials"},
    )
    assert bad_grant.status_code == 422

    blank_name = journey_client.patch(
        f"/api/v1/auth/session/{fresh_id}/name", json={"name": ""}, headers=fresh_headers
    )
    assert blank_name.status_code == 422
