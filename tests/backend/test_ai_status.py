"""GET /api/v1/ai/status — degraded-state matrix (PLAN 8.1).

Every case runs through the real route + AIStatusService with an
``httpx.MockTransport``; no real oMLX endpoint is ever contacted. All
degraded states are HTTP 200 with an explicit schema.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx

# conftest.py sits in tests/backend and is importable because pytest inserts
# that directory into sys.path (no __init__.py package layout).
from conftest import make_ai_service
from tests.backend.client import TestClient

from server.ai.service import AIStatusService


def _json_response(payload: object, status: int = 200) -> httpx.Response:
    return httpx.Response(status_code=status, json=payload)  # type: ignore[arg-type]


def test_not_configured_returns_without_http(
    client: TestClient, override_ai: Callable[[TestClient, AIStatusService], object]
) -> None:
    def _must_not_be_called():  # pragma: no cover
        raise AssertionError("no HTTP request may happen when AI is unconfigured")

    service = AIStatusService(base_url=None, client_factory=_must_not_be_called)  # type: ignore[arg-type]
    with override_ai(client, service):
        response = client.get("/api/v1/ai/status")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "not_configured"
    assert body["provider"] == "omlx"
    assert body["endpoint"] is None
    assert body["qwen_model"] is None
    assert body["models"] == []
    assert body["capabilities"] == {"chat": False, "embedding": False, "rerank": False}
    assert body["error_code"] == "not_configured"
    assert body["message"] == "AI endpoint is not configured"


def _raise_connect_error(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


def test_connection_refused_maps_to_offline(
    client: TestClient, override_ai: Callable[[TestClient, AIStatusService], object]
) -> None:
    service = make_ai_service(_raise_connect_error)
    with override_ai(client, service):
        response = client.get("/api/v1/ai/status")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "offline"
    assert body["error_code"] == "connection_refused"
    assert body["qwen_model"] is None


def _raise_timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectTimeout("timed out", request=request)


def test_timeout_maps_to_offline(
    client: TestClient, override_ai: Callable[[TestClient, AIStatusService], object]
) -> None:
    service = make_ai_service(_raise_timeout)
    with override_ai(client, service):
        response = client.get("/api/v1/ai/status")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "offline"
    assert body["error_code"] == "timeout"
    assert body["message"] == "oMLX endpoint did not respond before timeout"


def test_http_error_does_not_leak_response_body(
    client: TestClient, override_ai: Callable[[TestClient, AIStatusService], object]
) -> None:
    def _http_500(request: httpx.Request) -> httpx.Response:
        return _json_response({"detail": "super-secret-internal-string"}, status=500)

    service = make_ai_service(_http_500)
    with override_ai(client, service):
        response = client.get("/api/v1/ai/status")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "offline"
    assert body["error_code"] == "http_error"
    assert "super-secret-internal-string" not in response.text


def test_invalid_json_maps_to_offline(
    client: TestClient, override_ai: Callable[[TestClient, AIStatusService], object]
) -> None:
    def _non_json(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not json at all</html>")

    service = make_ai_service(_non_json)
    with override_ai(client, service):
        response = client.get("/api/v1/ai/status")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "offline"
    assert body["error_code"] == "invalid_response"


def test_invalid_schema_maps_to_offline(
    client: TestClient, override_ai: Callable[[TestClient, AIStatusService], object]
) -> None:
    for payload in (
        {"data": "not-a-list"},
        {"data": [{"no_id": True}]},
        {"data": [{"id": 42}]},
        {"data": [{"id": ""}]},
        ["not", "an", "object"],
    ):
        service = make_ai_service(lambda _req, p=payload: _json_response(p))
        with override_ai(client, service):
            response = client.get("/api/v1/ai/status")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "offline", payload
        assert body["error_code"] == "invalid_response", payload


def test_connected_discovers_qwen_and_models(
    client: TestClient,
    override_ai: Callable[[TestClient, AIStatusService], object],
    load_json: Callable[[str], object],
) -> None:
    payload = load_json("ai_models_connected.json")
    service = make_ai_service(lambda _req: _json_response(payload))
    with override_ai(client, service):
        response = client.get("/api/v1/ai/status")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "connected"
    assert body["provider"] == "omlx"
    assert body["endpoint"] == "http://127.0.0.1:8000/v1"
    assert body["qwen_model"] == "Qwen3.5-4B-Instruct-4bit"
    assert body["error_code"] is None
    assert [m["id"] for m in body["models"]] == [
        "Qwen3.5-4B-Instruct-4bit",
        "text-embedding-nomic-embed-text-v1.5",
    ]
    assert body["models"][0]["owned_by"] == "omlx"
    # chat is core (Qwen discovered); embedding comes from explicit metadata.
    assert body["capabilities"] == {"chat": True, "embedding": True, "rerank": False}


def test_connected_without_qwen_stays_connected(
    client: TestClient, override_ai: Callable[[TestClient, AIStatusService], object]
) -> None:
    payload = {
        "data": [
            {"id": "llama-3.1-8b-instruct", "owned_by": "omlx"},
            {"id": "text-embedding-nomic-embed-text", "owned_by": "omlx"},
        ]
    }
    service = make_ai_service(lambda _req: _json_response(payload))
    with override_ai(client, service):
        response = client.get("/api/v1/ai/status")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "connected"
    assert body["qwen_model"] is None
    assert body["error_code"] == "no_matching_model"
    assert body["message"] is not None
    assert body["capabilities"]["chat"] is False
    assert body["capabilities"]["embedding"] is False


def test_optional_capabilities_are_whitelisted_and_never_take_status_offline(
    client: TestClient, override_ai: Callable[[TestClient, AIStatusService], object]
) -> None:
    """Malformed/unknown capability metadata degrades to safe defaults."""
    payload = {
        "data": [
            {"id": "Qwen3.5-4B-Instruct", "capabilities": {"chat": "yes", "evil": True}},
            {
                "id": "bge-reranker-v2-m3",
                "capabilities": {"rerank": True, "embedding": "nope"},
            },
        ]
    }
    service = make_ai_service(lambda _req: _json_response(payload))
    with override_ai(client, service):
        response = client.get("/api/v1/ai/status")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "connected"
    assert body["qwen_model"] == "Qwen3.5-4B-Instruct"
    assert body["capabilities"] == {"chat": True, "embedding": False, "rerank": True}
    # Per-model capability parse: whitelist + booleans only.
    qwen_entry = next(m for m in body["models"] if m["id"].startswith("Qwen"))
    assert qwen_entry["capabilities"] == {
        "chat": False,
        "embedding": False,
        "rerank": False,
    }


def test_endpoint_redaction_strips_credentials_and_query(
    client: TestClient, override_ai: Callable[[TestClient, AIStatusService], object]
) -> None:
    secret_url = "http://user:supersecret@127.0.0.1:8000/v1?token=leakme"
    service = make_ai_service(
        lambda _req: _json_response({"data": [{"id": "Qwen3.5-4B-Instruct"}]}),
        base_url=secret_url,
    )
    with override_ai(client, service):
        response = client.get("/api/v1/ai/status")
    assert response.status_code == 200
    body = response.json()
    assert body["endpoint"] == "http://127.0.0.1:8000/v1"
    assert "supersecret" not in response.text
    assert "leakme" not in response.text


def test_ai_never_causes_500_for_degraded_states(
    client: TestClient, override_ai: Callable[[TestClient, AIStatusService], object]
) -> None:
    service = make_ai_service(_raise_connect_error)
    with override_ai(client, service):
        response = client.get("/api/v1/ai/status")
    assert response.status_code == 200
    assert "traceback" not in response.text.lower()
