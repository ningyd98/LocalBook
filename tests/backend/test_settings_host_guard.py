"""``/api/v1/settings`` host allow-list: local-only by default, opt-in otherwise.

The settings routes (including the AI key/model endpoints) require a loopback
peer *and* a trusted ``Host``. Reaching the app through a reverse proxy that
preserves the public Host header therefore needs
``server.settings_trusted_hosts``; this file pins both directions so the
default stays local-only.
"""

from __future__ import annotations

import pytest
from tests.backend.client import TestClient

from server.api.main import create_app
from server.config import Settings

PUBLIC_HOST = "note.ningyd.com"
AI = {"enabled": True, "base_url": "http://127.0.0.1:9/v1", "chat_model": "auto"}


def _app(tmp_path, *, trusted: list[str]):
    root = tmp_path / "vault"
    root.mkdir()
    settings = Settings(
        server={"settings_trusted_hosts": trusted},
        vault={"root": root, "watcher_enabled": False},
        scheduler={"enabled": False},
        ai={"base_url": None},
    )
    return create_app(settings)


@pytest.fixture
def untrusted_client(tmp_path):
    with TestClient(_app(tmp_path, trusted=[]), base_url=f"http://{PUBLIC_HOST}") as client:
        yield client


@pytest.fixture
def trusted_client(tmp_path):
    with TestClient(_app(tmp_path, trusted=[PUBLIC_HOST]), base_url=f"http://{PUBLIC_HOST}") as client:
        yield client


def test_public_host_is_rejected_by_default(untrusted_client) -> None:
    response = untrusted_client.get("/api/v1/settings")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "settings_local_only"


def test_public_host_rejects_ai_key_update_by_default(untrusted_client) -> None:
    state = untrusted_client.get("/api/v1/settings")
    assert state.status_code == 403
    response = untrusted_client.patch(
        "/api/v1/settings",
        json={"expected_revision": 1, "ai": {**AI, "api_key": "sk-secret"}},
    )
    assert response.status_code == 403


def test_trusted_host_can_read_and_write_settings(trusted_client) -> None:
    snapshot = trusted_client.get("/api/v1/settings")
    assert snapshot.status_code == 200, snapshot.text
    assert snapshot.json()["ai"]["api_key_set"] is False

    saved = trusted_client.patch(
        "/api/v1/settings",
        json={
            "expected_revision": snapshot.json()["revision"],
            "ai": {**AI, "api_key": "sk-secret"},
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["ai"]["api_key_set"] is True
    assert "sk-secret" not in saved.text


def test_trusted_host_still_requires_loopback_peer(tmp_path) -> None:
    """A trusted Host alone is not enough: the peer must be loopback."""
    app = _app(tmp_path, trusted=[PUBLIC_HOST])
    with TestClient(app, base_url=f"http://{PUBLIC_HOST}", client=("203.0.113.7", 51234)) as client:
        response = client.get("/api/v1/settings")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "settings_local_only"


def test_browser_origin_on_the_public_host_is_accepted(trusted_client) -> None:
    """Same-origin browser requests pass even though the proxy terminates TLS.

    ``request.base_url`` reports http behind the proxy; the Origin check must
    compare normalized scheme+host instead of the raw base_url string.
    """
    response = trusted_client.get(
        "/api/v1/settings", headers={"Origin": f"https://{PUBLIC_HOST}"}
    )
    assert response.status_code == 200, response.text


def test_foreign_origin_is_rejected(trusted_client) -> None:
    response = trusted_client.get(
        "/api/v1/settings", headers={"Origin": "https://evil.example.com"}
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "settings_local_only"


def test_forwarded_loopback_hop_allows_a_remote_peer(tmp_path) -> None:
    """nginx -> frp -> Vite: the peer is public but the last XFF hop is loopback."""
    app = _app(tmp_path, trusted=[PUBLIC_HOST])
    with TestClient(app, base_url=f"http://{PUBLIC_HOST}", client=("203.0.113.7", 51234)) as client:
        denied = client.get("/api/v1/settings")
        assert denied.status_code == 403
        allowed = client.get(
            "/api/v1/settings",
            headers={"X-Forwarded-For": "203.0.113.7, 127.0.0.1"},
        )
    assert allowed.status_code == 200, allowed.text
