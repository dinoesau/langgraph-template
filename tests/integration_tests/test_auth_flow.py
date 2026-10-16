"""S4 auth integration: full lifecycle on an ephemeral database.

Uses a throwaway SQLite file plus TestClient, never the development database.
Covers register, login, session create, scoped chat, rename, delete, 401s,
and cross-user isolation end to end.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from src.api.app import create_app
from src.core.limiter import auth_rate_limiter
from src.models import Base

PASSWORD = "ChangeMe123!"


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[TestClient]:
    engine = create_engine(f"sqlite:///{tmp_path}/auth-flow.db")
    Base.metadata.create_all(engine)
    factory: sessionmaker[OrmSession] = sessionmaker(bind=engine)
    app = create_app()
    app.state.auth_session_factory = factory
    auth_rate_limiter.reset()
    yield TestClient(app)
    auth_rate_limiter.reset()


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


def test_full_lifecycle_register_login_session_chat_rename_delete(client: TestClient) -> None:
    _register(client, "ada@example.com")
    user_token = _login(client, "ada@example.com")
    user_headers = {"Authorization": f"Bearer {user_token}"}

    session_id, session_token = _open_session(client, user_token)
    headers = {"Authorization": f"Bearer {session_token}"}

    chat = client.post("/api/v1/chat", json={"message": "hello"}, headers=headers)
    assert chat.status_code == 200
    assert chat.json() == {"reply": "hello", "thread_id": session_id}

    renamed = client.patch(
        f"/api/v1/auth/session/{session_id}/name", json={"name": "research"}, headers=headers
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "research"

    listed = client.get("/api/v1/auth/sessions", headers=user_headers)
    assert listed.status_code == 200
    assert [item["session_id"] for item in listed.json()["sessions"]] == [session_id]

    deleted = client.delete(f"/api/v1/auth/session/{session_id}", headers=headers)
    assert deleted.status_code == 200
    assert client.get("/api/v1/auth/sessions", headers=user_headers).json() == {"sessions": []}


def test_unauthenticated_and_cross_user_requests_rejected(client: TestClient) -> None:
    _register(client, "ada@example.com")
    _register(client, "bob@example.com")
    ada_token = _login(client, "ada@example.com")
    bob_token = _login(client, "bob@example.com")

    assert client.post("/api/v1/chat", json={"message": "hi"}).status_code == 401
    assert client.get("/api/v1/auth/sessions").status_code == 401
    assert client.post("/api/v1/auth/session", json={}).status_code == 401

    ada_id, ada_session = _open_session(client, ada_token)
    _, bob_session = _open_session(client, bob_token)
    bob_headers = {"Authorization": f"Bearer {bob_session}"}

    assert (
        client.patch(
            f"/api/v1/auth/session/{ada_id}/name", json={"name": "hijack"}, headers=bob_headers
        ).status_code
        == 403
    )
    assert client.delete(f"/api/v1/auth/session/{ada_id}", headers=bob_headers).status_code == 403

    ada_headers = {"Authorization": f"Bearer {ada_session}"}
    assert (
        client.post("/api/v1/chat", json={"message": "hi"}, headers=ada_headers).status_code == 200
    )
    bob_list = client.get(
        "/api/v1/auth/sessions", headers={"Authorization": f"Bearer {bob_token}"}
    ).json()
    assert [item["session_id"] for item in bob_list["sessions"]] != [ada_id]
