"""Unit tests for auth router: lifecycle plus scope matrix over HTTP."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from core.config import get_settings
from services.auth_security import create_token


@pytest.fixture(name="client")
def client_fixture(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-0123456789abcdef01234567")
    monkeypatch.setenv("POSTGRES_URI", "")
    get_settings.cache_clear()
    app = create_app()
    with TestClient(app) as client:
        yield client
    get_settings.cache_clear()


def _register(client: TestClient, email: str = "a@b.co") -> str:
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "ChangeMe123!", "username": "you"},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["access_token"])


def _login(client: TestClient, email: str = "a@b.co") -> str:
    response = client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": "ChangeMe123!", "grant_type": "password"},
    )
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])


def _session(client: TestClient, user_token: str) -> tuple[str, str]:
    response = client.post(
        "/api/v1/auth/session", headers={"Authorization": f"Bearer {user_token}"}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return str(body["session_id"]), str(body["token"])


def test_register_login_session_list(client: TestClient) -> None:
    user_token = _register(client)
    assert _login(client) != ""
    session_id, session_token = _session(client, user_token)
    assert session_id
    assert session_token
    listed = client.get("/api/v1/auth/sessions", headers={"Authorization": f"Bearer {user_token}"})
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()] == [session_id]


def test_register_duplicate_is_409(client: TestClient) -> None:
    _register(client)
    dup = client.post(
        "/api/v1/auth/register",
        json={"email": "a@b.co", "password": "ChangeMe123!", "username": "you"},
    )
    assert dup.status_code == 409


def test_register_race_is_409(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from sqlalchemy.exc import IntegrityError

    import api.v1.auth as auth_module

    async def _raise(*args: object, **kwargs: object) -> object:
        raise IntegrityError("INSERT", {}, Exception("boom"))

    monkeypatch.setattr(auth_module, "create_user", _raise)
    dup = client.post(
        "/api/v1/auth/register",
        json={"email": "race@b.co", "password": "ChangeMe123!", "username": "you"},
    )
    assert dup.status_code == 409


def test_login_bad_password_is_401(client: TestClient) -> None:
    _register(client)
    bad = client.post(
        "/api/v1/auth/login",
        data={"username": "a@b.co", "password": "Wrong123!x", "grant_type": "password"},
    )
    assert bad.status_code == 401


def test_register_weak_password_is_422(client: TestClient) -> None:
    weak = client.post(
        "/api/v1/auth/register",
        json={"email": "w@b.co", "password": "weakpass", "username": "you"},
    )
    assert weak.status_code == 422


def test_user_token_cannot_rename(client: TestClient) -> None:
    user_token = _register(client)
    session_id, _ = _session(client, user_token)
    denied = client.patch(
        f"/api/v1/auth/session/{session_id}/name",
        json={"name": "nope"},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert denied.status_code == 403


def test_cross_user_session_is_403(client: TestClient) -> None:
    alice_token = _register(client, "alice@b.co")
    alice_sid, _ = _session(client, alice_token)
    bob_token = _register(client, "bob@b.co")
    _, bob_session_token = _session(client, bob_token)
    denied = client.patch(
        f"/api/v1/auth/session/{alice_sid}/name",
        json={"name": "hijack"},
        headers={"Authorization": f"Bearer {bob_session_token}"},
    )
    assert denied.status_code == 403


def test_rename_and_delete_roundtrip(client: TestClient) -> None:
    user_token = _register(client)
    session_id, session_token = _session(client, user_token)
    headers = {"Authorization": f"Bearer {session_token}"}
    renamed = client.patch(
        f"/api/v1/auth/session/{session_id}/name", json={"name": "research"}, headers=headers
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "research"
    deleted = client.delete(f"/api/v1/auth/session/{session_id}", headers=headers)
    assert deleted.status_code == 204
    listed = client.get("/api/v1/auth/sessions", headers={"Authorization": f"Bearer {user_token}"})
    assert listed.json() == []


def test_challenge_only_on_401(client: TestClient) -> None:
    user_token = _register(client)
    bad = user_token[:-2] + ("ab" if not user_token.endswith("ab") else "cd")
    denied = client.get("/api/v1/auth/sessions", headers={"Authorization": f"Bearer {bad}"})
    assert denied.status_code == 401
    assert denied.headers.get("www-authenticate") == 'Bearer realm="auth" scope="user"'
    forbidden = client.patch(
        "/api/v1/auth/session/any-id/name",
        json={"name": "x"},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert forbidden.status_code == 403
    assert "www-authenticate" not in forbidden.headers
def test_expired_token_is_401(client: TestClient) -> None:
    secret = "test-secret-0123456789abcdef01234567"
    expired = create_token("1", "user", None, -1, secret=secret, algorithm="HS256")
    response = client.get("/api/v1/auth/sessions", headers={"Authorization": f"Bearer {expired}"})
    assert response.status_code == 401


def test_tampered_token_is_401(client: TestClient) -> None:
    user_token = _register(client)
    tampered = user_token[:-2] + ("ab" if not user_token.endswith("ab") else "cd")
    response = client.get("/api/v1/auth/sessions", headers={"Authorization": f"Bearer {tampered}"})
    assert response.status_code == 401


def test_session_token_on_user_route_is_403(client: TestClient) -> None:
    user_token = _register(client)
    _, session_token = _session(client, user_token)
    response = client.get(
        "/api/v1/auth/sessions", headers={"Authorization": f"Bearer {session_token}"}
    )
    assert response.status_code == 403


def test_deleted_session_token_chat_is_404(client: TestClient) -> None:
    user_token = _register(client)
    session_id, session_token = _session(client, user_token)
    headers = {"Authorization": f"Bearer {session_token}"}
    assert client.delete(f"/api/v1/auth/session/{session_id}", headers=headers).status_code == 204
    assert client.post("/api/v1/chat", json={"message": "hi"}, headers=headers).status_code == 404
    listed = client.get("/api/v1/auth/sessions", headers={"Authorization": f"Bearer {user_token}"})
    assert listed.json() == []


def test_session_token_without_sid_is_403(client: TestClient) -> None:
    secret = "test-secret-0123456789abcdef01234567"
    sidless = create_token("1", "session", None, 30, secret=secret, algorithm="HS256")
    response = client.post(
        "/api/v1/chat",
        json={"message": "hi"},
        headers={"Authorization": f"Bearer {sidless}"},
    )
    assert response.status_code == 403


def test_non_int_sub_is_401(client: TestClient) -> None:
    secret = "test-secret-0123456789abcdef01234567"
    bad_sub = create_token("abc", "user", None, 30, secret=secret, algorithm="HS256")
    response = client.get("/api/v1/auth/sessions", headers={"Authorization": f"Bearer {bad_sub}"})
    assert response.status_code == 401
