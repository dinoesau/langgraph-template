import json
import logging

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.api.app import create_app
from src.core.config import Settings, get_settings
from src.core.security import SESSION_SCOPE, create_token

app = create_app()
client = TestClient(app)


def _session_headers(thread_id: str) -> dict[str, str]:
    token, _ = create_token(
        "1", SESSION_SCOPE, get_settings().JWT_SECRET_KEY, 7, extra={"sid": thread_id}
    )
    return {"Authorization": f"Bearer {token}"}


def test_health_ok_and_request_id() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "dependencies" in body
    assert set(body["dependencies"]) >= {"loop", "store", "cache"}
    assert "X-Request-ID" in response.headers
    assert response.headers["X-Request-ID"]


def test_chat_echo_and_generated_thread() -> None:
    response = client.post(
        "/api/v1/chat", json={"message": "hello"}, headers=_session_headers("thread-new-1")
    )
    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == "hello"
    assert body["thread_id"] == "thread-new-1"
    assert "X-Request-ID" in response.headers


def test_chat_thread_passthrough() -> None:
    response = client.post(
        "/api/v1/chat",
        json={"message": "hi", "thread_id": "abc123"},
        headers=_session_headers("abc123"),
    )
    assert response.status_code == 200
    assert response.json() == {"reply": "hi", "thread_id": "abc123"}


def test_chat_rejects_thread_mismatch() -> None:
    response = client.post(
        "/api/v1/chat",
        json={"message": "hi", "thread_id": "other"},
        headers=_session_headers("abc123"),
    )
    assert response.status_code == 403


def test_chat_requires_session_token() -> None:
    assert client.post("/api/v1/chat", json={"message": "hello"}).status_code == 401


def test_chat_stream_sse() -> None:
    response = client.post(
        "/api/v1/chat/stream",
        json={"message": "hello", "thread_id": "tid-1"},
        headers=_session_headers("tid-1"),
    )
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    assert "X-Request-ID" in response.headers
    text = response.text
    assert "delta" in text
    assert "hello" in text
    assert "thread_id" in text
    assert "[DONE]" in text
    payloads = [
        json.loads(line[len("data: ") :])
        for line in text.splitlines()
        if line.startswith("data: ") and line.strip() != "data: [DONE]"
    ]
    assert payloads
    assert all(item.get("thread_id") == "tid-1" for item in payloads)


def test_access_log_binds_thread_id() -> None:
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    access_logger = logging.getLogger("api.access")
    handler = _Capture()
    access_logger.addHandler(handler)
    # Alembic runs fileConfig with disable_existing_loggers on upgrade, which
    # flips this logger off when migration tests ran first; force it back on.
    was_disabled = access_logger.disabled
    access_logger.disabled = False
    try:
        response = client.post(
            "/api/v1/chat",
            json={"message": "hi", "thread_id": "log-tid-1"},
            headers=_session_headers("log-tid-1"),
        )
    finally:
        access_logger.removeHandler(handler)
        access_logger.disabled = was_disabled
    assert response.status_code == 200
    assert response.json()["thread_id"] == "log-tid-1"
    assert "X-Request-ID" in response.headers
    assert any(getattr(record, "thread_id", None) == "log-tid-1" for record in records)


def test_chat_empty_message_422_envelope() -> None:
    response = client.post(
        "/api/v1/chat", json={"message": ""}, headers=_session_headers("tid-422")
    )
    assert response.status_code == 422
    body = response.json()
    assert "error" in body
    assert "code" in body["error"]
    assert "message" in body["error"]
    assert "request_id" in body
    assert "X-Request-ID" in response.headers
    assert body["request_id"] == response.headers["X-Request-ID"]


def test_unknown_route_404_envelope() -> None:
    response = client.get("/does-not-exist")
    assert response.status_code == 404
    body = response.json()
    assert "error" in body
    assert "request_id" in body
    assert "X-Request-ID" in response.headers


def test_request_id_echo_and_uniqueness() -> None:
    echoed = client.get("/health", headers={"X-Request-ID": "fixed-id-123"})
    assert echoed.headers["X-Request-ID"] == "fixed-id-123"
    first = client.get("/health")
    second = client.get("/health")
    assert first.headers["X-Request-ID"]
    assert second.headers["X-Request-ID"]
    assert first.headers["X-Request-ID"] != second.headers["X-Request-ID"]


def test_settings_rejects_bad_env() -> None:
    with pytest.raises(ValidationError):
        Settings(APP_ENV="bogus")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        Settings(PORT=99999)


def test_settings_defaults() -> None:
    get_settings.cache_clear()
    fresh = get_settings()
    assert fresh.PORT == 8000
