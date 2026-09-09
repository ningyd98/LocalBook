"""Degradation & isolation tests (PLAN-M6 §9.1.9–9.1.10): no false 5xx, no leaks."""

from __future__ import annotations

import httpx
from tests.backend.client import TestClient

from server.ai.service import AIStatusService
from server.api import dependencies
from server.api.main import create_app
from server.config import AISettings, Settings


def _boom_transport_service(base_url: str = "http://127.0.0.1:8000/v1") -> AIStatusService:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    return AIStatusService(base_url=base_url, transport=httpx.MockTransport(handler))


def test_ai_offline_never_breaks_health() -> None:
    client = TestClient(create_app())
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/api/v1/health").json() == {"status": "ok"}


def test_ai_not_configured_status_is_200_while_health_ok() -> None:
    settings = Settings(ai=AISettings(enabled=True, base_url=None))
    client = TestClient(create_app(settings=settings))
    assert client.get("/api/v1/health").status_code == 200
    body = client.get("/api/v1/ai/status").json()
    assert body["status"] == "not_configured"


def test_offline_error_payload_never_leaks_bodies() -> None:
    app = create_app()
    app.dependency_overrides[dependencies.get_ai_status_service] = lambda: _boom_transport_service()
    response = TestClient(app, raise_server_exceptions=False).get("/api/v1/ai/status")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "offline"
    assert body["error_code"] == "connection_refused"
    assert body["message"] == "oMLX endpoint is unreachable"


def test_unexpected_ai_probe_failure_is_offline_not_500() -> None:
    app = create_app()

    class Exploding:
        async def list_models(self):  # pragma: no cover - defensive probe path
            raise ValueError("boom")

    service = AIStatusService(
        base_url="http://127.0.0.1:8000/v1", client_factory=Exploding  # type: ignore[arg-type]
    )
    app.dependency_overrides[dependencies.get_ai_status_service] = lambda: service
    response = TestClient(app, raise_server_exceptions=False).get("/api/v1/ai/status")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "offline"
    assert body["error_code"] == "unknown"


def test_defensive_unhandled_error_has_no_traceback_or_secret() -> None:
    """The one true 500 path is generic and safe (no traceback/body leak)."""
    app = create_app()

    class CrashStatus:
        async def check(self):  # pragma: no cover - intentional crash probe
            raise RuntimeError("deep internal secret")

    app.dependency_overrides[dependencies.get_ai_status_service] = lambda: CrashStatus()  # type: ignore[return-value]
    response = TestClient(app, raise_server_exceptions=False).get("/api/v1/ai/status")
    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "internal_error"
    assert "traceback" not in response.text.lower()
    assert "deep internal secret" not in response.text
