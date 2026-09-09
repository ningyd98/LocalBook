"""AI API key + model discovery contracts.

Covers the feature added for the settings UI:

* ``AISettings.api_key`` normalization (blank means "no auth").
* ``GET /v1/models`` sending ``Authorization: Bearer <key>`` and reporting
  HTTP 401/403 as the explicit ``auth_error`` code instead of a generic
  ``http_error``.
* ``POST /api/v1/settings/ai/models`` returning the dropdown model list and
  reusing the stored key when the request omits ``api_key``.
* ``PATCH /api/v1/settings`` persisting the key without ever echoing it back.

A real loopback HTTP server (not a mock transport) enforces the bearer token,
so the header really travels over the wire and the 401 path is exercised
end-to-end.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from tests.backend.client import TestClient

from server.ai.adapters.omlx_client import DiscoveryError, OMLXDiscoveryConfig, OMLXModelDiscoveryClient
from server.ai.service import AIStatusService
from server.api.main import create_app
from server.config import AISettings

TOKEN = "sk-test-secret"


class _ModelsHandler(BaseHTTPRequestHandler):
    """Serves GET /models, requiring ``Authorization: Bearer <TOKEN>``."""

    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        # The fixture base_url already ends in /v1, so the client requests /v1/models.
        if self.path.rstrip("/") != "/v1/models":
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            body = b'{"error":"unauthorized"}'
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        body = json.dumps(
            {"object": "list", "data": [{"id": "qwen3.5-4b", "owned_by": "local"}]}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:  # keep test output quiet
        return


@pytest.fixture
def auth_endpoint() -> str:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ModelsHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        server.server_close()


# --------------------------------------------------------------------------
# Settings model
# --------------------------------------------------------------------------


def test_api_key_defaults_to_none_and_blank_normalizes() -> None:
    assert AISettings().api_key is None
    assert AISettings(api_key="   ").api_key is None
    assert AISettings(api_key="  sk-abc  ").api_key == "sk-abc"


# --------------------------------------------------------------------------
# Discovery client / status service
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discovery_sends_bearer_and_lists_models(auth_endpoint: str) -> None:
    client = OMLXModelDiscoveryClient(
        OMLXDiscoveryConfig(base_url=auth_endpoint, api_key=TOKEN, request_timeout_seconds=5)
    )
    models = await client.list_models()
    assert [m.id for m in models] == ["qwen3.5-4b"]


@pytest.mark.asyncio
async def test_discovery_without_key_reports_auth_error(auth_endpoint: str) -> None:
    client = OMLXModelDiscoveryClient(
        OMLXDiscoveryConfig(base_url=auth_endpoint, request_timeout_seconds=5)
    )
    with pytest.raises(DiscoveryError) as caught:
        await client.list_models()
    assert caught.value.code == "auth_error"


@pytest.mark.asyncio
async def test_discovery_with_wrong_key_reports_auth_error(auth_endpoint: str) -> None:
    client = OMLXModelDiscoveryClient(
        OMLXDiscoveryConfig(base_url=auth_endpoint, api_key="wrong", request_timeout_seconds=5)
    )
    with pytest.raises(DiscoveryError) as caught:
        await client.list_models()
    assert caught.value.code == "auth_error"


@pytest.mark.asyncio
async def test_status_service_with_key_is_connected(auth_endpoint: str) -> None:
    service = AIStatusService(
        base_url=auth_endpoint, api_key=TOKEN, request_timeout_seconds=5, chat_model="auto"
    )
    status = await service.check()
    assert status.status == "connected"
    assert status.selected_model == "qwen3.5-4b"


@pytest.mark.asyncio
async def test_status_service_without_key_is_offline_auth_error(auth_endpoint: str) -> None:
    service = AIStatusService(base_url=auth_endpoint, request_timeout_seconds=5)
    status = await service.check()
    assert status.status == "offline"
    assert status.error_code == "auth_error"


# --------------------------------------------------------------------------
# Settings API
# --------------------------------------------------------------------------


def _settings_client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """A live runtime with a real vault: PATCH /settings needs both."""
    root = tmp_path / "vault"
    root.mkdir()
    config = tmp_path / "config" / "settings.json"
    monkeypatch.setenv("LOCALNOTE_SETTINGS_FILE", str(config))
    from server.config import Settings

    settings = Settings(
        vault={"root": root, "watcher_enabled": False},
        scheduler={"enabled": False},
        ai={"base_url": None},
    )
    app = create_app(settings)
    with TestClient(app) as client:
        yield client, config


def test_models_endpoint_lists_models_with_submitted_key(auth_endpoint: str) -> None:
    client = _settings_client()
    response = client.post(
        "/api/v1/settings/ai/models",
        json={"enabled": True, "base_url": auth_endpoint, "api_key": TOKEN, "chat_model": "auto"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "connected"
    assert [m["id"] for m in payload["models"]] == ["qwen3.5-4b"]
    assert payload["selected_model"] == "qwen3.5-4b"
    assert payload["error_code"] is None


def test_models_endpoint_reports_auth_error_without_key(auth_endpoint: str) -> None:
    client = _settings_client()
    response = client.post(
        "/api/v1/settings/ai/models",
        json={"enabled": True, "base_url": auth_endpoint, "chat_model": "auto"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "offline"
    assert payload["error_code"] == "auth_error"
    assert payload["http_status"] == 401
    assert payload["models"] == []


def test_models_endpoint_rejects_blank_endpoint() -> None:
    client = _settings_client()
    response = client.post(
        "/api/v1/settings/ai/models",
        json={"enabled": True, "base_url": "", "chat_model": "auto"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "not_configured"


def test_patch_persists_key_but_never_echoes_it(workspace) -> None:
    client, config = workspace
    snapshot = client.get("/api/v1/settings").json()
    revision = snapshot["revision"]
    assert snapshot["ai"]["api_key_set"] is False
    assert "api_key" not in snapshot["ai"]

    response = client.patch(
        "/api/v1/settings",
        json={
            "expected_revision": revision,
            "ai": {"enabled": True, "base_url": "http://127.0.0.1:8000/v1",
                   "api_key": TOKEN, "chat_model": "auto"},
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ai"]["api_key_set"] is True
    assert "api_key" not in payload["ai"]
    assert TOKEN not in response.text
    # The key is persisted for the next process start (file is 0600).
    saved = json.loads(config.read_text())
    assert saved["ai"]["api_key"] == TOKEN
    assert config.stat().st_mode & 0o777 == 0o600


def test_patch_without_key_keeps_the_stored_one(workspace) -> None:
    client, _config = workspace
    first = client.patch(
        "/api/v1/settings",
        json={
            "expected_revision": client.get("/api/v1/settings").json()["revision"],
            "ai": {"enabled": True, "base_url": "http://127.0.0.1:8000/v1",
                   "api_key": TOKEN, "chat_model": "auto"},
        },
    ).json()
    # A second update omitting api_key (the UI never re-sends a stored key).
    second = client.patch(
        "/api/v1/settings",
        json={
            "expected_revision": first["revision"],
            "ai": {"enabled": True, "base_url": "http://127.0.0.1:8000/v1",
                   "chat_model": "qwen3.5-4b"},
        },
    )
    assert second.status_code == 200, second.text
    assert second.json()["ai"]["api_key_set"] is True
    assert second.json()["ai"]["chat_model"] == "qwen3.5-4b"


def test_patch_with_blank_key_clears_it(workspace) -> None:
    client, config = workspace
    first = client.patch(
        "/api/v1/settings",
        json={
            "expected_revision": client.get("/api/v1/settings").json()["revision"],
            "ai": {"enabled": True, "base_url": "http://127.0.0.1:8000/v1",
                   "api_key": TOKEN, "chat_model": "auto"},
        },
    ).json()
    cleared = client.patch(
        "/api/v1/settings",
        json={
            "expected_revision": first["revision"],
            "ai": {"enabled": True, "base_url": "http://127.0.0.1:8000/v1",
                   "api_key": "", "chat_model": "auto"},
        },
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["ai"]["api_key_set"] is False
    assert json.loads(config.read_text())["ai"]["api_key"] is None


def test_models_endpoint_reuses_stored_key_when_request_omits_it(auth_endpoint: str, workspace) -> None:
    client, _config = workspace
    stored = client.patch(
        "/api/v1/settings",
        json={
            "expected_revision": client.get("/api/v1/settings").json()["revision"],
            "ai": {"enabled": True, "base_url": auth_endpoint,
                   "api_key": TOKEN, "chat_model": "auto"},
        },
    )
    assert stored.status_code == 200, stored.text
    response = client.post(
        "/api/v1/settings/ai/models",
        json={"enabled": True, "base_url": auth_endpoint, "chat_model": "auto"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "connected"
    assert [m["id"] for m in response.json()["models"]] == ["qwen3.5-4b"]


def test_models_endpoint_reports_whether_a_key_was_sent(auth_endpoint: str) -> None:
    """The UI needs to tell "no key configured" from "key was rejected"."""
    client = _settings_client()
    no_key = client.post(
        "/api/v1/settings/ai/models",
        json={"enabled": True, "base_url": auth_endpoint, "chat_model": "auto"},
    ).json()
    assert no_key["error_code"] == "auth_error"
    assert no_key["key_sent"] is False

    wrong_key = client.post(
        "/api/v1/settings/ai/models",
        json={"enabled": True, "base_url": auth_endpoint,
              "api_key": "sk-wrong", "chat_model": "auto"},
    ).json()
    assert wrong_key["error_code"] == "auth_error"
    assert wrong_key["key_sent"] is True

    ok = client.post(
        "/api/v1/settings/ai/models",
        json={"enabled": True, "base_url": auth_endpoint,
              "api_key": TOKEN, "chat_model": "auto"},
    ).json()
    assert ok["status"] == "connected"
    assert ok["key_sent"] is True
