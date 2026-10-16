import json
import logging

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import ValidationError

from src.api.app import create_app
from src.core.config import Settings, get_settings
from src.services.llm.service import llm_service

app = create_app()
client = TestClient(app)


@pytest.fixture(autouse=True)
def _fake_llm_echo(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep HTTP tests hermetic: the loop answers through a fake LLM echo."""

    async def _echo(messages: object, **kwargs: object) -> AIMessage:
        text = ""
        assert isinstance(messages, list)
        for message in reversed(messages):
            if isinstance(message, HumanMessage):
                text = message.content if isinstance(message.content, str) else ""
                break
        return AIMessage(content=text)

    monkeypatch.setattr(llm_service, "call", _echo)


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
    response = client.post("/api/v1/chat", json={"message": "hello"})
    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == "hello"
    assert body["thread_id"]
    assert "X-Request-ID" in response.headers


def test_chat_thread_passthrough() -> None:
    response = client.post("/api/v1/chat", json={"message": "hi", "thread_id": "abc123"})
    assert response.status_code == 200
    assert response.json() == {"reply": "hi", "thread_id": "abc123"}


def test_chat_stream_sse() -> None:
    response = client.post("/api/v1/chat/stream", json={"message": "hello", "thread_id": "tid-1"})
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
    try:
        response = client.post("/api/v1/chat", json={"message": "hi", "thread_id": "log-tid-1"})
    finally:
        access_logger.removeHandler(handler)
    assert response.status_code == 200
    assert response.json()["thread_id"] == "log-tid-1"
    assert "X-Request-ID" in response.headers
    assert any(getattr(record, "thread_id", None) == "log-tid-1" for record in records)


def test_chat_empty_message_422_envelope() -> None:
    response = client.post("/api/v1/chat", json={"message": ""})
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
