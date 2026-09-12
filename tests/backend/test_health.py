"""GET /api/v1/health — fixed response, AI/Vault independent (PLAN 8.1)."""

from __future__ import annotations

from tests.backend.client import TestClient


def test_health_happy_path(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"status": "ok"}


def test_health_body_is_exactly_ok(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    # Exact health contract — no extra fields.
    assert response.text == '{"status":"ok"}'


def test_health_route_is_isolated_from_ai(
    safe_client: TestClient, override_ai
) -> None:
    """Health stays 200 even when the AI dependency itself blows up."""

    class _BrokenService:
        async def check(self):  # pragma: no cover - only called if AI is hit
            raise RuntimeError("ai must not be touched by /health")

    with override_ai(safe_client, _BrokenService()):  # type: ignore[arg-type]
        response = safe_client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


def test_unknown_route_returns_json_404(client: TestClient) -> None:
    response = client.get("/api/v1/does-not-exist")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
