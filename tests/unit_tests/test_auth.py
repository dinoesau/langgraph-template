"""S4 auth lifecycle endpoint tests: register, login, sessions, scoped chat.

Seam: TestClient over ``create_app()`` with ``app.state.auth_session_factory``
pointed at an ephemeral SQLite file per test; no network or Postgres needed.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

import src.api.v1.auth as auth_module
from src.api.app import create_app
from src.core.limiter import RateLimiter, auth_rate_limiter
from src.models import Base

PASSWORD = "ChangeMe123!"


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[TestClient]:
    engine = create_engine(f"sqlite:///{tmp_path}/auth.db")
    Base.metadata.create_all(engine)
    factory: sessionmaker[OrmSession] = sessionmaker(bind=engine)
    app = create_app()
    app.state.auth_session_factory = factory
    auth_rate_limiter.reset()
    yield TestClient(app)
    auth_rate_limiter.reset()


def _register(
    client: TestClient,
    email: str = "ada@example.com",
    password: str = PASSWORD,
    username: str | None = "ada",
) -> Response:
    payload: dict[str, object] = {"email": email, "password": password}
    if username is not None:
        payload["username"] = username
    return client.post("/api/v1/auth/register", json=payload)


def _login(client: TestClient, email: str, password: str = PASSWORD) -> Response:
    return client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": password, "grant_type": "password"},
    )


def _user_token(client: TestClient, email: str = "ada@example.com") -> str:
    assert _register(client, email=email).status_code == 200
    response = _login(client, email)
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, dict)
    return str(body["access_token"])


def _session_token(client: TestClient, user_token: str, name: object = None) -> tuple[str, str]:
    payload = {} if name is None else {"name": name}
    response = client.post(
        "/api/v1/auth/session",
        json=payload,
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, dict)
    return str(body["session_id"]), str(body["token"])


def test_register_returns_user_token_and_hides_hash(client: TestClient) -> None:
    response = _register(client)
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, dict)
    assert body["email"] == "ada@example.com"
    assert body["username"] == "ada"
    assert body["user_id"]
    assert body["access_token"]
    assert body["expires_at"]
    assert "hash" not in response.text.lower()
    assert "ChangeMe123!" not in response.text


def test_register_rejects_weak_password_422_envelope(client: TestClient) -> None:
    response = _register(client, password="weak")
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert body["request_id"] == response.headers["X-Request-ID"]


def test_register_rejects_duplicate_email_409(client: TestClient) -> None:
    assert _register(client).status_code == 200
    response = _register(client)
    assert response.status_code == 409
    assert response.json()["request_id"] == response.headers["X-Request-ID"]


def test_register_rejects_bad_email_422(client: TestClient) -> None:
    response = _register(client, email="not-an-email")
    assert response.status_code == 422


def test_login_returns_user_token(client: TestClient) -> None:
    _register(client)
    response = _login(client, "ada@example.com")
    assert response.status_code == 200
    body = response.json()
    assert body["access_token"]
    assert body["expires_at"]


def test_login_accepts_email_form_field(client: TestClient) -> None:
    _register(client)
    response = client.post(
        "/api/v1/auth/login", data={"email": "ada@example.com", "password": PASSWORD}
    )
    assert response.status_code == 200


def test_login_wrong_password_401_envelope(client: TestClient) -> None:
    _register(client)
    response = _login(client, "ada@example.com", "WrongPass123!")
    assert response.status_code == 401
    body = response.json()
    assert "error" in body
    assert body["request_id"] == response.headers["X-Request-ID"]


def test_login_unknown_user_401(client: TestClient) -> None:
    response = _login(client, "nobody@example.com")
    assert response.status_code == 401


def test_session_create_returns_session_token(client: TestClient) -> None:
    token = _user_token(client)
    session_id, session_token = _session_token(client, token)
    assert session_id
    assert session_token
    assert session_token != token


def test_session_create_requires_user_token(client: TestClient) -> None:
    assert client.post("/api/v1/auth/session", json={}).status_code == 401
    token = _user_token(client)
    session_id, session_token = _session_token(client, token)
    assert session_id
    forbidden = client.post(
        "/api/v1/auth/session",
        json={},
        headers={"Authorization": f"Bearer {session_token}"},
    )
    assert forbidden.status_code == 403


def test_list_shows_only_caller_sessions(client: TestClient) -> None:
    ada = _user_token(client, "ada@example.com")
    bob = _user_token(client, "bob@example.com")
    _session_token(client, ada)
    headers = {"Authorization": f"Bearer {ada}"}
    assert len(client.get("/api/v1/auth/sessions", headers=headers).json()["sessions"]) == 1
    bob_headers = {"Authorization": f"Bearer {bob}"}
    assert client.get("/api/v1/auth/sessions", headers=bob_headers).json() == {"sessions": []}
    assert client.get("/api/v1/auth/sessions").status_code == 401


def test_rename_with_session_token(client: TestClient) -> None:
    token = _user_token(client)
    session_id, session_token = _session_token(client, token)
    headers = {"Authorization": f"Bearer {session_token}"}
    response = client.patch(
        f"/api/v1/auth/session/{session_id}/name", json={"name": "research"}, headers=headers
    )
    assert response.status_code == 200
    assert response.json()["name"] == "research"


def test_rename_enforces_matching_scope(client: TestClient) -> None:
    token = _user_token(client)
    session_id, session_token = _session_token(client, token)
    other_id, _ = _session_token(client, token)
    headers = {"Authorization": f"Bearer {session_token}"}
    assert (
        client.patch(
            f"/api/v1/auth/session/{other_id}/name", json={"name": "x"}, headers=headers
        ).status_code
        == 403
    )
    user_headers = {"Authorization": f"Bearer {token}"}
    assert (
        client.patch(
            f"/api/v1/auth/session/{session_id}/name",
            json={"name": "x"},
            headers=user_headers,
        ).status_code
        == 403
    )
    assert client.patch(
        "/api/v1/auth/session/missing-id/name", json={"name": "x"}, headers=headers
    ).status_code in (403, 404)


def test_delete_removes_session(client: TestClient) -> None:
    token = _user_token(client)
    session_id, session_token = _session_token(client, token)
    headers = {"Authorization": f"Bearer {session_token}"}
    assert client.delete(f"/api/v1/auth/session/{session_id}", headers=headers).status_code == 200
    assert client.get(
        "/api/v1/auth/sessions", headers={"Authorization": f"Bearer {token}"}
    ).json() == {"sessions": []}
    assert client.delete(f"/api/v1/auth/session/{session_id}", headers=headers).status_code == 404


def test_cross_user_isolation(client: TestClient) -> None:
    ada = _user_token(client, "ada@example.com")
    bob = _user_token(client, "bob@example.com")
    ada_id, ada_session = _session_token(client, ada)
    _, bob_session = _session_token(client, bob)
    bob_headers = {"Authorization": f"Bearer {bob_session}"}
    assert (
        client.patch(
            f"/api/v1/auth/session/{ada_id}/name", json={"name": "hijack"}, headers=bob_headers
        ).status_code
        == 403
    )
    assert client.delete(f"/api/v1/auth/session/{ada_id}", headers=bob_headers).status_code == 403
    ada_headers = {"Authorization": f"Bearer {ada_session}"}
    assert client.delete(f"/api/v1/auth/session/{ada_id}", headers=ada_headers).status_code == 200


def test_chat_requires_session_token(client: TestClient) -> None:
    token = _user_token(client)
    session_id, session_token = _session_token(client, token)
    assert client.post("/api/v1/chat", json={"message": "hi"}).status_code == 401
    user_headers = {"Authorization": f"Bearer {token}"}
    assert client.post(
        "/api/v1/chat", json={"message": "hi"}, headers=user_headers
    ).status_code in (
        401,
        403,
    )
    headers = {"Authorization": f"Bearer {session_token}"}
    response = client.post("/api/v1/chat", json={"message": "hi"}, headers=headers)
    assert response.status_code == 200
    assert response.json() == {"reply": "hi", "thread_id": session_id}


def test_chat_rejects_thread_mismatch(client: TestClient) -> None:
    token = _user_token(client)
    _, session_token = _session_token(client, token)
    headers = {"Authorization": f"Bearer {session_token}"}
    response = client.post(
        "/api/v1/chat", json={"message": "hi", "thread_id": "other"}, headers=headers
    )
    assert response.status_code == 403


def test_chat_stream_requires_session_token(client: TestClient) -> None:
    assert client.post("/api/v1/chat/stream", json={"message": "hi"}).status_code == 401
    token = _user_token(client)
    _, session_token = _session_token(client, token)
    headers = {"Authorization": f"Bearer {session_token}"}
    response = client.post("/api/v1/chat/stream", json={"message": "hi"}, headers=headers)
    assert response.status_code == 200
    assert "[DONE]" in response.text


def test_register_rate_limited_429_envelope(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        auth_module, "auth_rate_limiter", RateLimiter(max_attempts=2, window_seconds=60)
    )
    assert _register(client, email="a1@example.com").status_code == 200
    assert _register(client, email="a2@example.com").status_code == 200
    limited = _register(client, email="a3@example.com")
    assert limited.status_code == 429
    assert limited.json()["request_id"] == limited.headers["X-Request-ID"]
