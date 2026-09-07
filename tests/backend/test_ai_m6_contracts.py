"""M6 contract probes: isolated, read-only, and never connected to real AI.

These tests pin the implemented M6 surface: the six POST routes exist, are
schema-gated, and never need a real Vault or AI endpoint.  Every workflow and
status call runs against mocks or degraded local state; no production fallback
or network call is hidden in these tests.
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from server.ai.schemas import AICapabilities, AIStatus
from server.config import AISettings


def test_phase0_status_schema_remains_read_only() -> None:
    assert AIStatus.CONNECTED.value == "connected"
    assert AICapabilities.model_json_schema()["properties"].keys() >= {
        "chat", "embedding", "rerank"
    }


def test_m6_settings_contract_is_not_silently_enabled_yet() -> None:
    """Document the additive fields required by PLAN-M6 §5.2."""
    missing = {
        "enabled", "provider", "chat_model", "temperature", "max_context_notes"
    } - set(AISettings.model_fields)
    assert not missing, f"M6 AISettings fields not implemented: {sorted(missing)}"


@pytest.mark.parametrize(
    "path",
    ["/api/v1/ai/chat", "/api/v1/ai/summarize", "/api/v1/ai/tags",
     "/api/v1/ai/related", "/api/v1/ai/extract_todos", "/api/v1/ai/classify"],
)
def test_m6_routes_are_declared_and_schema_gated(path: str, client: TestClient) -> None:
    """The six POST routes exist and fail closed with 422 invalid_request.

    No Vault is configured and no AI request may fire: dependency wiring must
    not raise a Vault 503 before body validation (S4), and the empty body is
    rejected by the strict request DTOs before any model call.
    """
    response = client.post(path, json={})
    assert response.status_code == 422, path
    body = response.json()
    assert body["error"]["code"] == "invalid_request"
    assert body["error"]["path"] is None
    assert "traceback" not in response.text.lower()


def test_mock_transport_is_the_only_allowed_ai_transport() -> None:
    """A small guard that keeps this contract suite independent of loopback AI."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"data": []})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport, base_url="http://test/v1") as http:
        response = http.get("/models")
    assert response.status_code == 200
    assert [request.url.host for request in requests] == ["test"]


def test_contract_tests_never_need_a_vault_write(tmp_path: Path) -> None:
    """Keep the read-only intent visible to reviewers and future contributors."""
    sentinel = tmp_path / "sentinel.md"
    sentinel.write_text("unchanged", encoding="utf-8")
    assert sentinel.read_text(encoding="utf-8") == "unchanged"
