"""Real temporary vaults exercise configuration commits and cross-vault isolation."""
import base64
import json

import pytest
from fastapi.testclient import TestClient as RawClient

from server.api.main import create_app
from server.config import Settings
from server.index.service import DerivedIndexService
from server.runtime import ConfigRepository, SESSION_HEADER
from tests.backend.client import TestClient

ORIGINAL = b'\xef\xbb\xbf# Same\r\n\r\noriginal\r\n'
AI = {"enabled": False, "base_url": "http://127.0.0.1:8234/v1/", "chat_model": "manual-model"}


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    first, second = tmp_path / "a", tmp_path / "b"
    for root in (first, second):
        root.mkdir()
        (root / "same.md").write_bytes(ORIGINAL)
    config = tmp_path / "config" / "settings.json"
    monkeypatch.setenv("LOCALNOTE_SETTINGS_FILE", str(config))
    settings = Settings(vault={"root": first, "watcher_enabled": False}, scheduler={"enabled": False}, ai={"base_url": None})
    app = create_app(settings)
    with TestClient(app) as client:
        yield client, app.state.runtime, first, second, config, settings


def switch(client, root, **changes):
    state = client.get("/api/v1/settings").json()
    body = {"root": str(root), "expected_revision": state["revision"], "vault_session_id": state["vault_session_id"], **changes}
    return client.post("/api/v1/settings/vault/switch", json=body)


def update(client, **changes):
    return client.patch("/api/v1/settings", json={"expected_revision": client.get("/api/v1/settings").json()["revision"], "ai": AI, **changes})


def test_switch_persists_and_blocks_identical_file_from_old_page(workspace):
    client, runtime, first, second, config, settings = workspace
    old = runtime.snapshot()
    original = client.get("/api/v1/vault/file", params={"path": "same.md"}).json()
    response = switch(client, second)
    assert response.status_code == 200, response.text
    new = response.json()
    assert new["revision"] == old["revision"] + 1
    assert new["vault_session_id"] != old["vault_session_id"]
    assert new["vault"]["root"] == str(second) and new["changing"] is False
    attack = client.patch("/api/v1/vault/file", json={"path": "same.md", "expected_sha256": original["sha256"], "content_base64": base64.b64encode(b"old-page-write").decode()})
    assert attack.status_code == 409
    assert attack.json()["error"]["code"] == "vault_session_changed"
    assert (first / "same.md").read_bytes() == (second / "same.md").read_bytes() == ORIGINAL
    assert client.get("/api/v1/vault/files").status_code == 409
    client.headers[SESSION_HEADER] = new["vault_session_id"]
    assert client.get("/api/v1/vault/file", params={"path": "same.md"}).status_code == 200
    assert config.stat().st_mode & 0o777 == 0o600
    saved = json.loads(config.read_text())
    assert saved["vault_root"] == str(second)
    # Explicit saved values take precedence over the original injected settings.
    restored = create_app(settings)
    assert restored.state.settings.vault.root == second
    assert restored.state.runtime.revision == new["revision"]


def test_missing_session_write_is_rejected(workspace):
    client, runtime, *_ = workspace
    with RawClient(create_app(runtime.settings)) as old_client:
        response = old_client.post("/api/v1/index/rebuild")
        assert response.status_code == 428
        assert response.json()["error"]["code"] == "vault_session_required"
        assert old_client.get("/api/v1/vault/files").status_code == 200


@pytest.mark.parametrize("kind", ["missing", "file", "relative", "symlink"])
def test_invalid_target_keeps_original_workspace(workspace, kind):
    client, runtime, first, second, config, _ = workspace
    target = {"missing": first.parent / "missing", "file": first / "same.md", "relative": "relative"}.get(kind)
    if kind == "symlink":
        target = first.parent / "alias"
        target.symlink_to(second, target_is_directory=True)
    before = runtime.snapshot()
    response = switch(client, target)
    assert response.status_code == 422
    assert runtime.snapshot() == before
    assert not config.exists()
    assert client.get("/api/v1/vault/file", params={"path": "same.md"}).status_code == 200


def test_live_request_and_background_lock_block_reconfiguration(workspace):
    client, runtime, first, second, config, _ = workspace
    with runtime.request(runtime.session_id, write=False):
        assert switch(client, second).json()["error"]["code"] == "runtime_busy"
        assert update(client).json()["error"]["code"] == "runtime_busy"
    with runtime.scheduler._locks.acquire("daily_organizer", "active-test-run"):
        assert switch(client, second).json()["error"]["code"] == "runtime_busy"
        assert update(client).json()["error"]["code"] == "runtime_busy"
    assert runtime.settings.vault.root == first and not config.exists()


def test_transition_blocks_scoped_requests_and_new_triggers(workspace):
    client, runtime, _, second, *_ = workspace
    with runtime.transition(runtime.revision):
        assert client.get("/api/v1/vault/files").status_code == 503
        assert switch(client, second).status_code == 409
        assert client.get("/api/v1/health").status_code == 200
        assert runtime.scheduler._begin_run(task="daily_organizer", trigger="scheduled") is None
    assert client.get("/api/v1/vault/files").status_code == 200


@pytest.mark.parametrize("failure", ["index", "services", "save", "replace", "scheduler_start"])
def test_failed_switch_restores_services_and_configuration(workspace, monkeypatch, failure):
    client, runtime, first, second, config, _ = workspace
    assert update(client).status_code == 200
    saved = config.read_bytes()
    before = runtime.snapshot()
    owners = runtime.lifecycle, runtime.agent, runtime.scheduler
    def fail(*args, **kwargs):
        raise OSError("injected failure")
    if failure == "index":
        monkeypatch.setattr(DerivedIndexService, "rebuild", fail)
    elif failure == "services":
        monkeypatch.setattr(runtime, "_services", fail)
    elif failure == "save":
        monkeypatch.setattr(runtime.repository, "save", fail)
    elif failure == "replace":
        monkeypatch.setattr("server.runtime.os.replace", fail)
    else:
        monkeypatch.setattr(type(runtime.scheduler), "start", fail)
    response = switch(client, second)
    assert response.status_code in (422, 500), response.text
    assert runtime.snapshot() == before
    assert (runtime.lifecycle, runtime.agent, runtime.scheduler) == owners
    assert runtime.scheduler._reconfiguring is False
    assert config.read_bytes() == saved
    assert client.get("/api/v1/vault/file", params={"path": "same.md"}).status_code == 200
    assert (first / "same.md").read_bytes() == (second / "same.md").read_bytes() == ORIGINAL


def test_ai_apply_replaces_all_consumers_without_changing_vault(workspace):
    client, runtime, _, _, config, _ = workspace
    old_session, old_agent = runtime.session_id, runtime.agent
    response = update(client)
    assert response.status_code == 200, response.text
    assert response.json()["ai"] == {**AI, "base_url": AI["base_url"].rstrip("/"), "api_key_set": False}
    # The stored key is write-only: the snapshot reports only whether one exists.
    assert "api_key" not in response.json()["ai"]
    assert runtime.agent is not old_agent and runtime.scheduler.agent_service is runtime.agent
    assert runtime.session_id == old_session
    assert client.get("/api/v1/ai/status").json()["status"] == "not_configured"
    assert json.loads(config.read_text())["ai"]["chat_model"] == "manual-model"


def test_ai_write_failure_retains_previous_services(workspace, monkeypatch):
    client, runtime, _, _, config, _ = workspace
    before = runtime.snapshot()
    owners = runtime.agent, runtime.scheduler
    def fail(*args):
        raise OSError("configuration disk unavailable")
    monkeypatch.setattr(runtime.repository, "save", fail)
    assert update(client).status_code == 500
    assert runtime.snapshot() == before
    assert (runtime.agent, runtime.scheduler) == owners
    assert not config.exists()


def test_revision_and_session_conflicts(workspace):
    client, runtime, _, second, *_ = workspace
    assert update(client, expected_revision=999).json()["error"]["code"] == "settings_conflict"
    assert switch(client, second, vault_session_id="expired-session").json()["error"]["code"] == "vault_session_changed"


@pytest.mark.parametrize("url", ["file:///tmp/notes", "http://user:password@localhost:9000/v1", "http://localhost/v1?key=hidden", "http://localhost:bad/v1", "broken-url"])
def test_invalid_endpoints_rejected_without_saving(workspace, url):
    client, _, _, _, config, _ = workspace
    assert update(client, ai={**AI, "base_url": url}).status_code == 422
    assert client.post("/api/v1/settings/ai/test", json={**AI, "base_url": url}).status_code == 422
    assert not config.exists()


def test_connection_test_only_discovers_models(workspace, monkeypatch):
    from server.ai.adapters.openai_compatible import OpenAICompatibleAdapter
    from server.ai.schemas import DiscoveredModel
    client, runtime, _, _, config, _ = workspace
    calls = []
    async def models(self):
        calls.append("models")
        return [DiscoveredModel(id="manual-model", capabilities={"chat": True})]
    async def chat(self, *args, **kwargs):
        pytest.fail("Connection tests must never invoke inference")
    monkeypatch.setattr(OpenAICompatibleAdapter, "list_models", models)
    monkeypatch.setattr(OpenAICompatibleAdapter, "chat", chat)
    before = runtime.snapshot()
    response = client.post("/api/v1/settings/ai/test", json=AI)
    assert response.status_code == 200
    assert response.json()["selected_model"] == "manual-model"
    assert calls == ["models"] and runtime.snapshot() == before
    assert not config.exists()


def test_settings_enforce_host_origin_json_and_loopback(workspace):
    client, runtime, *_ = workspace
    assert client.get("/api/v1/settings", headers={"host": "untrusted.example"}).status_code == 403
    assert client.get("/api/v1/settings", headers={"origin": "https://untrusted.example"}).status_code == 403
    assert client.patch("/api/v1/settings", content='{}', headers={"content-type": "text/plain"}).status_code == 415
    with RawClient(create_app(runtime.settings), client=("192.0.2.3", 6000)) as remote:
        assert remote.get("/api/v1/settings").status_code == 403


def test_same_vault_is_a_noop_and_configuration_cannot_enter_vault(workspace):
    client, runtime, first, _, config, _ = workspace
    before = runtime.snapshot()
    assert switch(client, first).json() == before
    response = switch(client, first.parent)
    assert response.status_code == 422
    assert not config.exists() and runtime.snapshot() == before


def test_instance_ports_have_separate_default_files(monkeypatch):
    monkeypatch.delenv("LOCALNOTE_SETTINGS_FILE", raising=False)
    first = ConfigRepository.for_settings(Settings(server={"port": 3780}))
    second = ConfigRepository.for_settings(Settings(server={"port": 3790}))
    assert first.path != second.path
    assert "127.0.0.1-3780" in str(first.path)
    assert "127.0.0.1-3790" in str(second.path)


def test_manual_discovered_model_without_optional_capabilities_is_supported():
    from server.ai.capabilities import resolve_chat_model
    from server.ai.schemas import DiscoveredModel
    assert resolve_chat_model([DiscoveredModel(id="custom-model")], "custom-model") == "custom-model"


@pytest.mark.asyncio
async def test_status_reports_actual_configured_model():
    from server.ai.service import AIStatusService
    from server.ai.schemas import DiscoveredModel
    class Discovery:
        async def list_models(self):
            return [DiscoveredModel(id="custom-model")]
    result = await AIStatusService(base_url="http://127.0.0.1:8234/v1", chat_model="custom-model", client_factory=Discovery).check()
    assert result.selected_model == "custom-model" and result.error_code is None
