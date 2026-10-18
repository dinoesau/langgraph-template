"""Unit tests for the HTTP skeleton.

TestClient runs the app in-process: no network and no containers.
"""

import pytest
from fastapi.testclient import TestClient

from api.app import create_app


@pytest.fixture(name="client")
def client_fixture() -> TestClient:
    return TestClient(create_app())


def test_health_reports_readiness(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "checks": {"graph": "loaded", "settings": "loaded"},
    }
    assert response.headers["X-Request-ID"]


def test_chat_delegates_to_loop_for_invoke(client: TestClient) -> None:
    response = client.post("/api/v1/chat", json={"message": "hello"})
    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == "Echo: hello"
    assert body["thread_id"]
    assert response.headers["X-Request-ID"]


def test_chat_streams_loop_reply(client: TestClient) -> None:
    with client.stream("POST", "/api/v1/chat/stream", json={"message": "hi"}) as response:
        assert response.status_code == 200
        assert response.headers["X-Request-ID"]
        text = "".join(response.iter_text())
    assert "Echo: hi" in text


def test_chat_rejects_blank_message(client: TestClient) -> None:
    response = client.post("/api/v1/chat", json={"message": "   "})
    assert response.status_code == 422
