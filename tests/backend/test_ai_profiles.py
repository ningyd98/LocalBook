"""Multi-provider AI profiles (PLAN-PROVIDERS).

Covers the whole feature contract:

* ``ProviderProfile`` normalization and bounds (id slug, blank key/URL, model).
* ``AISettings`` profile library semantics: synthesis from the flat fields,
  projection back onto them, upsert/remove pure helpers, duplicate rejection.
* ``ConfigRepository`` compatibility: the legacy single-provider file keeps its
  exact shape, a file with profiles round-trips, a broken library is a loud 503.
* ``Runtime`` transactions: activate/upsert/delete, revision conflicts, applied
  and last profile protection, and the guarantee that secrets never reach a
  response body.

No real provider is contacted: connection tests use a loopback HTTP server that
requires a bearer token, so the 401 path is exercised end to end.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from pydantic import ValidationError
from tests.backend.client import TestClient

from server.api.main import create_app
from server.config import AISettings, ProviderProfile, Settings
from server.runtime import ConfigRepository, SettingsError

TOKEN = "sk-profile-secret"


class _ModelsHandler(BaseHTTPRequestHandler):
    """Serves GET /v1/models, requiring ``Authorization: Bearer <TOKEN>``."""

    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
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
            {"object": "list", "data": [{"id": "qwen3.5-4b", "owned_by": "local"},
                                        {"id": "gpt-oss-20b", "owned_by": "local"}]}
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


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """A live runtime with a real vault and its own settings file."""
    root = tmp_path / "vault"
    root.mkdir()
    config = tmp_path / "config" / "settings.json"
    monkeypatch.setenv("LOCALNOTE_SETTINGS_FILE", str(config))
    settings = Settings(
        vault={"root": root, "watcher_enabled": False},
        scheduler={"enabled": False},
        ai={"base_url": "http://127.0.0.1:8234/v1", "chat_model": "gpt-oss-20b"},
    )
    app = create_app(settings)
    with TestClient(app) as client:
        yield client, config


def _profile(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": "deepseek",
        "name": "DeepSeek 官方",
        "kind": "openai",
        "base_url": "https://api.deepseek.com/v1",
        "chat_model": "deepseek-chat",
    }
    payload.update(overrides)
    return payload


def _revision(client: TestClient) -> int:
    return client.get("/api/v1/settings").json()["revision"]


def _add(client: TestClient, revision: int | None = None, activate: bool = True, **overrides: Any):
    return client.post(
        "/api/v1/settings/ai/profiles",
        json={
            "expected_revision": _revision(client) if revision is None else revision,
            "activate": activate,
            "provider": _profile(**overrides),
        },
    )


# --------------------------------------------------------------------------
# ProviderProfile model
# --------------------------------------------------------------------------


def test_profile_defaults_match_plan() -> None:
    profile = ProviderProfile(id="local")
    assert profile.name == ""
    assert profile.display_name == "local"
    assert profile.kind == "openai-compatible"
    assert profile.base_url == "http://127.0.0.1:8000/v1"
    assert profile.api_key is None
    assert profile.chat_model == "auto"
    assert profile.temperature == 0.1
    assert profile.max_output_tokens == 1200
    assert profile.request_timeout_seconds == 60.0
    assert profile.connect_timeout_seconds == 0.5
    assert profile.builtin is False
    assert profile.source == "settings"
    assert profile.from_env is False


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("DeepSeek", "deepseek"),
        ("  my-route  ", "my-route"),
        ("Open AI Route", "open-ai-route"),
        ("a_b.c-d", "a_b.c-d"),
    ],
)
def test_profile_id_is_normalized(raw: str, expected: str) -> None:
    assert ProviderProfile(id=raw).id == expected


@pytest.mark.parametrize(
    "bad_id",
    ["", "  ", "../etc/passwd", "a" * 65, "中文", "route/name", "-leading", ".dot"],
)
def test_profile_id_rejects_unsafe_values(bad_id: str) -> None:
    with pytest.raises(ValidationError):
        ProviderProfile(id=bad_id)


def test_profile_normalizes_endpoint_key_and_model() -> None:
    profile = ProviderProfile(
        id="x",
        name="  Spaced  ",
        base_url=" https://example.com/v1/ ",
        api_key="  sk-abc  ",
        chat_model="  gpt-4o-mini  ",
    )
    assert profile.name == "Spaced"
    assert profile.base_url == "https://example.com/v1"
    assert profile.api_key == "sk-abc"
    assert profile.chat_model == "gpt-4o-mini"


def test_profile_blank_endpoint_and_key_normalize_to_none() -> None:
    profile = ProviderProfile(id="x", base_url="   ", api_key="   ")
    assert profile.base_url is None
    assert profile.api_key is None


@pytest.mark.parametrize(
    "patch",
    [
        {"chat_model": ""},
        {"chat_model": "   "},
        {"temperature": 1.5},
        {"max_output_tokens": 32},
        {"request_timeout_seconds": 0},
        {"connect_timeout_seconds": 99},
        {"max_models_response_bytes": 0},
        {"kind": "anthropic"},
    ],
)
def test_profile_bounds_are_strict(patch: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ProviderProfile(id="x", **patch)


# --------------------------------------------------------------------------
# AISettings library semantics
# --------------------------------------------------------------------------


def test_fresh_settings_have_no_saved_profiles() -> None:
    """An untouched installation keeps the legacy single-provider layout (D3)."""
    ai = AISettings()
    assert ai.profiles == []
    assert ai.active_profile_id == "default"
    assert ai.synthesized_profiles()[0].id == "default"


def test_synthesized_profile_projects_flat_fields() -> None:
    ai = AISettings(base_url="http://127.0.0.1:8234/v1", chat_model="gpt-oss-20b")
    synthesized = ai.synthesized_profiles()
    assert len(synthesized) == 1
    assert synthesized[0].builtin is True
    assert synthesized[0].name == "127.0.0.1:8234"
    assert synthesized[0].chat_model == "gpt-oss-20b"
    # Synthesis is read-only: the library stays empty until something writes.
    assert ai.profiles == []


def test_synthesized_profile_without_endpoint_uses_model_name() -> None:
    assert AISettings(base_url=None, chat_model="qwen3.5-4b").synthesized_profiles()[0].name == "qwen3.5-4b"
    assert AISettings(base_url=None).synthesized_profiles()[0].name == "Local AI"


def test_effective_profile_projects_without_materializing_the_library() -> None:
    """Reading the applied route is a read: the library stays empty (D3)."""
    ai = AISettings(base_url="http://127.0.0.1:8234/v1", chat_model="gpt-oss-20b", temperature=0.4)
    profile = ai.effective_profile()
    assert profile.id == "default"
    assert profile.chat_model == "gpt-oss-20b"
    assert ai.profiles == []
    assert ai.synthesized_profiles()[0].id == "default"
    # Explicit materialization is what a write path uses.
    ai.ensure_active_profile()
    assert [p.id for p in ai.profiles] == ["default"]
    assert ai.profiles[0].temperature == 0.4


def test_effective_profile_projects_saved_entry_onto_flat_fields() -> None:
    ai = AISettings()
    saved = ai.with_profile(
        ProviderProfile(id="deepseek", kind="openai", base_url="https://api.deepseek.com/v1",
                        chat_model="deepseek-chat", temperature=0.7, max_output_tokens=4096)
    )
    assert saved.active_profile_id == "deepseek"
    assert saved.provider == "openai"
    assert saved.base_url == "https://api.deepseek.com/v1"
    assert saved.chat_model == "deepseek-chat"
    assert saved.temperature == 0.7
    assert saved.max_output_tokens == 4096
    # Switching back restores the local default route.
    back = saved.with_profile(saved.profiles[0], activate=True)
    assert back.active_profile_id == "default"
    assert back.provider == "omlx"
    assert back.base_url == "http://127.0.0.1:8000/v1"
    # The library itself carried both entries throughout.
    assert [p.id for p in back.profiles] == ["deepseek", "default"]


def test_with_profile_replaces_same_id_instead_of_duplicating() -> None:
    """The applied flat route materializes as ``default`` before the first upsert."""
    ai = AISettings().with_profile(ProviderProfile(id="a", chat_model="m1"))
    assert [p.id for p in ai.profiles] == ["default", "a"]
    again = ai.with_profile(ProviderProfile(id="a", chat_model="m2"))
    assert [p.id for p in again.profiles] == ["default", "a"]
    assert again.chat_model == "m2"
    assert again.active_profile_id == "a"


def test_without_profile_keeps_the_applied_route() -> None:
    ai = AISettings().with_profile(ProviderProfile(id="a", chat_model="m1"))
    two = ai.with_profile(ProviderProfile(id="b", chat_model="m2"), activate=False)
    assert two.active_profile_id == "a"
    removed = two.without_profile("b")
    assert [p.id for p in removed.profiles] == ["default", "a"]
    assert removed.active_profile_id == "a"


def test_unknown_active_profile_falls_back_to_the_first_entry() -> None:
    ai = AISettings.model_validate({
        "profiles": [{"id": "a"}, {"id": "b"}],
        "active_profile_id": "ghost",
    })
    assert ai.active_profile_id == "a"


def test_duplicate_profile_ids_are_rejected() -> None:
    with pytest.raises(ValidationError):
        AISettings.model_validate({"profiles": [{"id": "a"}, {"id": "a"}]})


def test_profile_library_is_bounded() -> None:
    with pytest.raises(ValidationError):
        AISettings.model_validate({"profiles": [{"id": f"p{index}"} for index in range(31)]})


def test_blank_active_profile_id_normalizes() -> None:
    assert AISettings(active_profile_id="   ").active_profile_id == "default"
    assert AISettings(active_profile_id="DeepSeek").active_profile_id == "deepseek"


# --------------------------------------------------------------------------
# ConfigRepository: legacy compatibility
# --------------------------------------------------------------------------


def test_legacy_file_shape_is_unchanged(tmp_path) -> None:
    """A single-provider save must not start writing profiles (D3)."""
    repository = ConfigRepository(tmp_path / "settings.json")
    settings = Settings(ai={"base_url": "http://127.0.0.1:8234/v1", "chat_model": "gpt-oss-20b"})
    repository.save(settings, 3)
    saved = json.loads((tmp_path / "settings.json").read_text())
    assert set(saved["ai"]) == {"enabled", "base_url", "api_key", "chat_model"}
    assert saved["revision"] == 3


def test_profiles_round_trip_through_the_settings_file(tmp_path) -> None:
    repository = ConfigRepository(tmp_path / "settings.json")
    base = Settings(ai={"base_url": "http://127.0.0.1:8234/v1", "chat_model": "gpt-oss-20b"})
    ai = base.ai.with_profile(
        ProviderProfile(id="deepseek", name="DeepSeek", kind="openai",
                        base_url="https://api.deepseek.com/v1", api_key=TOKEN,
                        chat_model="deepseek-chat", temperature=0.3)
    )
    repository.save(base.model_copy(update={"ai": ai}), 4)

    reloaded, revision = repository.load(Settings())
    assert revision == 4
    assert reloaded.ai.active_profile_id == "deepseek"
    assert reloaded.ai.chat_model == "deepseek-chat"
    assert reloaded.ai.temperature == 0.3
    assert reloaded.ai.api_key == TOKEN
    assert [p.id for p in reloaded.ai.profiles] == ["default", "deepseek"]
    assert reloaded.ai.profiles[1].name == "DeepSeek"


def test_legacy_file_without_profiles_still_loads(tmp_path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({
        "revision": 7,
        "vault_root": None,
        "ai": {"enabled": True, "base_url": "http://127.0.0.1:9000/v1",
               "api_key": None, "chat_model": "qwen3.5-4b"},
    }))
    settings, revision = ConfigRepository(path).load(Settings())
    assert revision == 7
    assert settings.ai.base_url == "http://127.0.0.1:9000/v1"
    assert settings.ai.chat_model == "qwen3.5-4b"
    assert settings.ai.profiles == []
    assert settings.ai.synthesized_profiles()[0].id == "default"


def test_unreadable_profile_library_is_a_loud_error(tmp_path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({
        "revision": 2,
        "vault_root": None,
        "ai": {"enabled": True, "base_url": None, "chat_model": "auto",
               "profiles": [{"id": "!! not a slug !!"}]},
    }))
    with pytest.raises(SettingsError) as caught:
        ConfigRepository(path).load(Settings())
    assert caught.value.status == 503
    assert caught.value.code == "settings_unreadable"


# --------------------------------------------------------------------------
# HTTP: reading profiles
# --------------------------------------------------------------------------


def test_list_profiles_returns_the_synthesized_default(workspace) -> None:
    client, _config = workspace
    response = client.get("/api/v1/settings/ai/profiles")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["active_profile_id"] == "default"
    profiles = payload["profiles"]
    assert len(profiles) == 1
    assert profiles[0]["id"] == "default"
    assert profiles[0]["is_active"] is True
    assert profiles[0]["builtin"] is True
    assert profiles[0]["base_url"] == "http://127.0.0.1:8234/v1"


def test_snapshot_carries_profiles_without_any_secret(workspace) -> None:
    client, _config = workspace
    added = _add(client, api_key=TOKEN)
    assert added.status_code == 200, added.text
    payload = added.json()
    ai = payload["ai"]
    assert ai["active_profile_id"] == "deepseek"
    assert [p["id"] for p in ai["profiles"]] == ["default", "deepseek"]
    active = next(p for p in ai["profiles"] if p["id"] == "deepseek")
    assert active["api_key_set"] is True
    assert active["is_active"] is True
    assert active["builtin"] is False
    for entry in ai["profiles"]:
        assert "api_key" not in entry
    assert TOKEN not in added.text


# --------------------------------------------------------------------------
# HTTP: switching
# --------------------------------------------------------------------------


def test_adding_a_profile_switches_to_it_and_persists(workspace) -> None:
    client, config = workspace
    added = _add(client, api_key=TOKEN, temperature=0.2)
    assert added.status_code == 200, added.text
    ai = added.json()["ai"]
    assert ai["base_url"] == "https://api.deepseek.com/v1"
    assert ai["chat_model"] == "deepseek-chat"
    assert ai["api_key_set"] is True
    assert config.stat().st_mode & 0o777 == 0o600

    saved = json.loads(config.read_text())
    assert saved["ai"]["active_profile_id"] == "deepseek"
    assert [p["id"] for p in saved["ai"]["profiles"]] == ["default", "deepseek"]
    assert saved["ai"]["profiles"][1]["api_key"] == TOKEN
    # The applied flat fields follow the switch on disk too.
    assert saved["ai"]["base_url"] == "https://api.deepseek.com/v1"
    assert saved["ai"]["chat_model"] == "deepseek-chat"


def test_switch_back_to_the_local_profile_restores_it(workspace) -> None:
    client, _config = workspace
    _add(client, api_key=TOKEN)
    revision = client.get("/api/v1/settings").json()["revision"]
    back = client.post("/api/v1/settings/ai/profiles/activate",
                       json={"profile_id": "default", "expected_revision": revision})
    assert back.status_code == 200, back.text
    ai = back.json()["ai"]
    assert ai["active_profile_id"] == "default"
    assert ai["base_url"] == "http://127.0.0.1:8234/v1"
    assert ai["chat_model"] == "gpt-oss-20b"
    # The saved deepseek route survives the switch with its key intact.
    deepseek = next(p for p in ai["profiles"] if p["id"] == "deepseek")
    assert deepseek["api_key_set"] is True
    assert deepseek["is_active"] is False


def test_activating_the_applied_profile_keeps_the_revision(workspace) -> None:
    """An idempotent switch must not burn a revision (no reload needed)."""
    client, _config = workspace
    revision = client.get("/api/v1/settings").json()["revision"]
    response = client.post("/api/v1/settings/ai/profiles/activate",
                           json={"profile_id": "default", "expected_revision": revision})
    assert response.status_code == 200, response.text
    assert response.json()["revision"] == revision


def test_save_without_activating_keeps_the_applied_route(workspace) -> None:
    client, _config = workspace
    revision = client.get("/api/v1/settings").json()["revision"]
    response = client.post("/api/v1/settings/ai/profiles", json={
        "expected_revision": revision,
        "activate": False,
        "provider": _profile(id="later", kind="openai", base_url="https://api.openai.com/v1"),
    })
    assert response.status_code == 200, response.text
    ai = response.json()["ai"]
    assert ai["active_profile_id"] == "default"
    assert ai["base_url"] == "http://127.0.0.1:8234/v1"
    assert [p["id"] for p in ai["profiles"]] == ["default", "later"]


def test_activating_an_unknown_profile_is_404(workspace) -> None:
    client, _config = workspace
    revision = client.get("/api/v1/settings").json()["revision"]
    response = client.post("/api/v1/settings/ai/profiles/activate",
                           json={"profile_id": "ghost", "expected_revision": revision})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ai_profile_not_found"


def test_stale_revision_is_a_conflict(workspace) -> None:
    client, _config = workspace
    revision = client.get("/api/v1/settings").json()["revision"]
    response = client.post("/api/v1/settings/ai/profiles/activate",
                           json={"profile_id": "default", "expected_revision": revision + 5})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "settings_conflict"
    # The refused request changed nothing.
    assert client.get("/api/v1/settings").json()["revision"] == revision


# --------------------------------------------------------------------------
# HTTP: editing and deleting
# --------------------------------------------------------------------------


def test_editing_a_profile_keeps_its_stored_key(workspace) -> None:
    client, config = workspace
    _add(client, api_key=TOKEN)
    revision = client.get("/api/v1/settings").json()["revision"]
    edited = client.post("/api/v1/settings/ai/profiles", json={
        "expected_revision": revision,
        "provider": _profile(chat_model="deepseek-reasoner", temperature=0.5),
    })
    assert edited.status_code == 200, edited.text
    ai = edited.json()["ai"]
    assert ai["chat_model"] == "deepseek-reasoner"
    stored = json.loads(config.read_text())["ai"]["profiles"]
    deepseek = next(p for p in stored if p["id"] == "deepseek")
    assert deepseek["api_key"] == TOKEN
    assert deepseek["chat_model"] == "deepseek-reasoner"
    assert deepseek["temperature"] == 0.5


def test_editing_a_profile_omitted_fields_are_inherited(workspace) -> None:
    client, _config = workspace
    _add(client, api_key=TOKEN)
    revision = client.get("/api/v1/settings").json()["revision"]
    edited = client.post("/api/v1/settings/ai/profiles", json={
        "expected_revision": revision,
        "provider": {"id": "deepseek", "name": "Renamed"},
    }).json()
    deepseek = next(p for p in edited["ai"]["profiles"] if p["id"] == "deepseek")
    assert deepseek["name"] == "Renamed"
    assert deepseek["base_url"] == "https://api.deepseek.com/v1"
    assert deepseek["chat_model"] == "deepseek-chat"
    assert deepseek["kind"] == "openai"


def test_blank_key_clears_the_stored_secret(workspace) -> None:
    client, config = workspace
    _add(client, api_key=TOKEN)
    revision = client.get("/api/v1/settings").json()["revision"]
    cleared = client.post("/api/v1/settings/ai/profiles", json={
        "expected_revision": revision,
        "provider": _profile(api_key=""),
    })
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["ai"]["api_key_set"] is False
    stored = json.loads(config.read_text())["ai"]
    assert stored["profiles"][1]["api_key"] is None


def test_deleting_the_applied_profile_is_refused(workspace) -> None:
    client, _config = workspace
    _add(client, api_key=TOKEN)
    revision = client.get("/api/v1/settings").json()["revision"]
    response = client.post("/api/v1/settings/ai/profiles/delete",
                           json={"profile_id": "deepseek", "expected_revision": revision})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ai_profile_active"


def test_deleting_an_idle_profile_works(workspace) -> None:
    client, config = workspace
    _add(client, api_key=TOKEN)
    _add(client, id="spare", activate=False, base_url="https://example.com/v1", chat_model="m")
    revision = client.get("/api/v1/settings").json()["revision"]
    response = client.post("/api/v1/settings/ai/profiles/delete",
                           json={"profile_id": "spare", "expected_revision": revision})
    assert response.status_code == 200, response.text
    assert [p["id"] for p in response.json()["ai"]["profiles"]] == ["default", "deepseek"]
    saved = json.loads(config.read_text())["ai"]["profiles"]
    assert [p["id"] for p in saved] == ["default", "deepseek"]


def test_deleting_the_last_profile_is_refused(workspace) -> None:
    client, _config = workspace
    revision = client.get("/api/v1/settings").json()["revision"]
    response = client.post("/api/v1/settings/ai/profiles/delete",
                           json={"profile_id": "default", "expected_revision": revision})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ai_profile_active"


def test_deleting_an_unknown_profile_is_404(workspace) -> None:
    client, _config = workspace
    revision = client.get("/api/v1/settings").json()["revision"]
    response = client.post("/api/v1/settings/ai/profiles/delete",
                           json={"profile_id": "ghost", "expected_revision": revision})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ai_profile_not_found"


def test_profile_cap_is_enforced(workspace) -> None:
    client, _config = workspace
    for index in range(29):
        response = _add(client, id=f"route{index}", activate=False,
                        base_url=f"https://example.com/{index}/v1")
        assert response.status_code == 200, response.text
    overflow = _add(client, id="onemore", activate=False)
    assert overflow.status_code == 422
    assert overflow.json()["error"]["code"] == "ai_profiles_full"


# --------------------------------------------------------------------------
# HTTP: validation and the local-only boundary
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "profile",
    [
        {"id": "../evil"},
        {"chat_model": ""},
        {"temperature": 3},
        {"kind": "anthropic"},
        {"base_url": "x" * 5000},
    ],
)
def test_invalid_payloads_are_rejected(workspace, profile: dict[str, Any]) -> None:
    client, _config = workspace
    revision = client.get("/api/v1/settings").json()["revision"]
    payload = _profile(**profile)
    payload.update(profile)
    response = client.post("/api/v1/settings/ai/profiles",
                           json={"expected_revision": revision, "provider": payload})
    assert response.status_code == 422, response.text


def test_unknown_payload_fields_are_rejected(workspace) -> None:
    client, _config = workspace
    revision = client.get("/api/v1/settings").json()["revision"]
    response = client.post("/api/v1/settings/ai/profiles", json={
        "expected_revision": revision,
        "provider": _profile(),
        "surprise": True,
    })
    assert response.status_code == 422


@pytest.mark.parametrize(
    "base_url",
    ["ftp://example.com/v1", "https://user:pw@example.com/v1", "https://example.com/v1?x=1"],
)
def test_invalid_endpoints_are_rejected(workspace, base_url: str) -> None:
    client, _config = workspace
    response = _add(client, base_url=base_url)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_ai_endpoint"


def test_profile_writes_require_json_content_type(workspace) -> None:
    client, _config = workspace
    response = client.post("/api/v1/settings/ai/profiles", content=b"{}")
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "settings_json_required"


# --------------------------------------------------------------------------
# HTTP: connection test
# --------------------------------------------------------------------------


def test_profile_test_uses_the_submitted_key(auth_endpoint: str, workspace) -> None:
    client, _config = workspace
    revision = client.get("/api/v1/settings").json()["revision"]
    response = client.post("/api/v1/settings/ai/profiles/test", json={
        "expected_revision": revision,
        "provider": _profile(base_url=auth_endpoint, api_key=TOKEN, chat_model="auto"),
    })
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "connected"
    assert [m["id"] for m in payload["models"]] == ["qwen3.5-4b", "gpt-oss-20b"]
    assert payload["selected_model"] == "qwen3.5-4b"
    assert payload["key_sent"] is True


def test_profile_test_without_key_reports_auth_error(auth_endpoint: str, workspace) -> None:
    client, _config = workspace
    revision = client.get("/api/v1/settings").json()["revision"]
    response = client.post("/api/v1/settings/ai/profiles/test", json={
        "expected_revision": revision,
        "provider": _profile(base_url=auth_endpoint),
    })
    payload = response.json()
    assert payload["status"] == "offline"
    assert payload["error_code"] == "auth_error"
    assert payload["http_status"] == 401
    assert payload["key_sent"] is False


def test_profile_test_does_not_write_anything(auth_endpoint: str, workspace) -> None:
    client, config = workspace
    revision = client.get("/api/v1/settings").json()["revision"]
    client.post("/api/v1/settings/ai/profiles/test", json={
        "expected_revision": revision,
        "provider": _profile(base_url=auth_endpoint, api_key=TOKEN),
    })
    after = client.get("/api/v1/settings").json()
    assert after["revision"] == revision
    assert [p["id"] for p in after["ai"]["profiles"]] == ["default"]
    assert not config.exists()


def test_profile_test_with_empty_endpoint_reports_not_configured(workspace) -> None:
    client, _config = workspace
    response = client.post("/api/v1/settings/ai/profiles/test", json={
        "provider": _profile(base_url=""),
    })
    assert response.status_code == 200
    assert response.json()["status"] == "not_configured"


# --------------------------------------------------------------------------
# /ai/status reports the applied profile
# --------------------------------------------------------------------------


def test_ai_status_names_the_applied_profile(workspace) -> None:
    client, _config = workspace
    before = client.get("/api/v1/ai/status").json()
    assert before["active_profile_id"] == "default"

    _add(client, api_key=TOKEN)
    after = client.get("/api/v1/ai/status").json()
    assert after["active_profile_id"] == "deepseek"
    assert after["active_profile_name"] == "DeepSeek 官方"
    assert after["active_profile_kind"] == "openai"


# --------------------------------------------------------------------------
# Flat PATCH keeps both views consistent
# --------------------------------------------------------------------------


def test_flat_patch_edits_the_applied_profile(workspace) -> None:
    client, config = workspace
    _add(client, api_key=TOKEN)
    revision = client.get("/api/v1/settings").json()["revision"]
    response = client.patch("/api/v1/settings", json={
        "expected_revision": revision,
        "ai": {"enabled": True, "base_url": "https://api.deepseek.com/beta",
               "chat_model": "deepseek-reasoner"},
    })
    assert response.status_code == 200, response.text
    saved = json.loads(config.read_text())["ai"]
    deepseek = next(p for p in saved["profiles"] if p["id"] == "deepseek")
    assert deepseek["base_url"] == "https://api.deepseek.com/beta"
    assert deepseek["chat_model"] == "deepseek-reasoner"
    assert deepseek["api_key"] == TOKEN
    # The idle local profile is untouched by an edit of the applied route.
    local = next(p for p in saved["profiles"] if p["id"] == "default")
    assert local["base_url"] == "http://127.0.0.1:8234/v1"


def test_flat_patch_before_any_profile_keeps_the_library_empty(workspace) -> None:
    """The single-provider layout must survive a flat edit byte-shape-wise.

    The snapshot always *shows* the synthesized applied route (D4), but nothing
    is persisted as a library entry until the user adds one (D3).
    """
    client, config = workspace
    revision = client.get("/api/v1/settings").json()["revision"]
    response = client.patch("/api/v1/settings", json={
        "expected_revision": revision,
        "ai": {"enabled": True, "base_url": "http://127.0.0.1:9999/v1", "chat_model": "auto"},
    })
    assert response.status_code == 200, response.text
    assert response.json()["ai"]["active_profile_id"] == "default"
    assert response.json()["ai"]["profiles"][0]["base_url"] == "http://127.0.0.1:9999/v1"
    saved = json.loads(config.read_text())["ai"]
    assert set(saved) == {"enabled", "base_url", "api_key", "chat_model"}
    assert saved["base_url"] == "http://127.0.0.1:9999/v1"
