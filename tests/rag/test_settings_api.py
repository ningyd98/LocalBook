"""M14 RAG settings: snapshot shape, secret handling and apply semantics."""

from __future__ import annotations

import re
from pathlib import Path

from server.api.main import create_app
from server.config import Settings
from server.runtime import SESSION_HEADER
from tests.backend.client import TestClient

WEB_SETTINGS_TS = (
    Path(__file__).resolve().parents[2] / "apps" / "web" / "src" / "api" / "settings.ts"
)


def _workspace(tmp_path: Path, **rag_overrides):
    root = tmp_path / "vault"
    root.mkdir()
    (root / "a.md").write_text("# A\n\n内容。\n", encoding="utf-8")
    rag = {"enabled": True, "embedding_provider": "hash", "embedding_dimension": 32}
    rag.update(rag_overrides)
    settings = Settings(vault={"root": root}, rag=rag)
    app = create_app(settings)
    return app, root


def test_snapshot_exposes_rag_settings_without_the_secret(tmp_path: Path) -> None:
    app, _ = _workspace(tmp_path)
    with TestClient(app) as client:
        client.headers[SESSION_HEADER] = app.state.runtime.session_id
        payload = client.get("/api/v1/settings").json()
    rag = payload["rag"]
    assert rag["enabled"] is True
    assert rag["embedding_provider"] == "hash"
    assert rag["embedding_model"] == "local-hash"
    assert rag["chunk_target_tokens"] == 800
    assert rag["context_top_k"] == 6
    # The stored key is never echoed; only whether one is set.
    assert "embedding_api_key" not in rag
    assert rag["embedding_api_key_set"] is False


def test_patch_rag_applies_and_bumps_revision(tmp_path: Path) -> None:
    app, _ = _workspace(tmp_path)
    with TestClient(app) as client:
        client.headers[SESSION_HEADER] = app.state.runtime.session_id
        before = client.get("/api/v1/settings").json()
        response = client.patch(
            "/api/v1/settings/rag",
            json={
                "expected_revision": before["revision"],
                "rag": {
                    "chunk_target_tokens": 600,
                    "chunk_max_tokens": 900,
                    "context_top_k": 4,
                },
            },
        )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["revision"] == before["revision"] + 1
    rag = payload["rag"]
    assert rag["chunk_target_tokens"] == 600
    assert rag["chunk_max_tokens"] == 900
    assert rag["context_top_k"] == 4
    # Everything else is untouched.
    assert rag["embedding_provider"] == "hash"
    assert rag["chunk_overlap_tokens"] == 100


def test_patch_rag_validates_values(tmp_path: Path) -> None:
    app, _ = _workspace(tmp_path)
    with TestClient(app) as client:
        client.headers[SESSION_HEADER] = app.state.runtime.session_id
        revision = client.get("/api/v1/settings").json()["revision"]
        response = client.patch(
            "/api/v1/settings/rag",
            json={"expected_revision": revision, "rag": {"chunk_max_tokens": 100}},
        )
    # chunk_max_tokens < chunk_target_tokens is rejected by the settings model.
    assert response.status_code == 422
    assert response.json()["error"]["code"] in {"invalid_rag_settings", "invalid_request"}


def test_patch_rag_rejects_unknown_fields(tmp_path: Path) -> None:
    app, _ = _workspace(tmp_path)
    with TestClient(app) as client:
        client.headers[SESSION_HEADER] = app.state.runtime.session_id
        revision = client.get("/api/v1/settings").json()["revision"]
        response = client.patch(
            "/api/v1/settings/rag",
            json={
                "expected_revision": revision,
                "rag": {"embedding_provider": "hash", "evil": True},
            },
        )
    assert response.status_code == 422


def test_patch_rag_keeps_the_vault_and_notes_untouched(tmp_path: Path) -> None:
    app, root = _workspace(tmp_path)
    note = root / "a.md"
    before = note.read_bytes()
    with TestClient(app) as client:
        client.headers[SESSION_HEADER] = app.state.runtime.session_id
        revision = client.get("/api/v1/settings").json()["revision"]
        response = client.patch(
            "/api/v1/settings/rag",
            json={
                "expected_revision": revision,
                "rag": {"embedding_provider": "none", "enabled": True},
            },
        )
        assert response.status_code == 200, response.text
        health = client.get("/api/v1/health")
        status = client.get("/api/v1/rag/index/status")
    assert note.read_bytes() == before
    assert health.status_code == 200
    # RAG still answers with its own status (lexical-only after embeddings are off).
    assert status.status_code in (200, 503)


def test_rag_settings_conflict_is_reported(tmp_path: Path) -> None:
    app, _ = _workspace(tmp_path)
    with TestClient(app) as client:
        client.headers[SESSION_HEADER] = app.state.runtime.session_id
        response = client.patch(
            "/api/v1/settings/rag",
            json={"expected_revision": 999, "rag": {"context_top_k": 3}},
        )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "settings_conflict"


# ---------------------------------------------------------------------------
# Roadmap item ③: link/graph retrieval is exposed but neutral by default
# ---------------------------------------------------------------------------


def test_link_settings_default_to_neutral(tmp_path: Path) -> None:
    """An unconfigured install keeps the pre-③ behaviour exactly."""
    app, _ = _workspace(tmp_path)
    with TestClient(app) as client:
        client.headers[SESSION_HEADER] = app.state.runtime.session_id
        rag = client.get("/api/v1/settings").json()["rag"]
        status = client.get("/api/v1/rag/index/status").json()
        stack = app.state.runtime.lifecycle.rag_stack
        assert stack is not None
        # Neutral means "no third retriever on the hybrid", not "a retriever that
        # returns nothing": the fused lists stay byte-for-byte the pre-③ ones.
        assert stack.retriever.link_enabled is False
    assert rag["link_retrieval_enabled"] is False
    assert rag["link_top_k"] == 20
    assert rag["wikilink_weight"] == 1.0
    assert rag["backlink_weight"] == 0.8
    assert rag["tag_weight"] == 0.6
    assert rag["graph_weight"] == 0.4
    # ``link_retrieval`` is a status field, never part of the settings snapshot.
    assert "link_retrieval" not in rag
    # The marker reports '' while nothing is wired, so the UI shows nothing
    # instead of claiming a link path exists.
    assert status["link_retrieval"] == ""


def test_link_settings_apply_and_bump_revision(tmp_path: Path) -> None:
    app, _ = _workspace(tmp_path)
    with TestClient(app) as client:
        client.headers[SESSION_HEADER] = app.state.runtime.session_id
        before = client.get("/api/v1/settings").json()
        response = client.patch(
            "/api/v1/settings/rag",
            json={
                "expected_revision": before["revision"],
                "rag": {
                    "link_retrieval_enabled": True,
                    "link_top_k": 25,
                    "wikilink_weight": 2.0,
                    "backlink_weight": 1.5,
                },
            },
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        # The derived RAG layer was rebuilt with the link path wired in.
        assert app.state.runtime.lifecycle.rag_stack.retriever.link_enabled is True
    assert payload["revision"] == before["revision"] + 1
    rag = payload["rag"]
    assert rag["link_retrieval_enabled"] is True
    assert rag["link_top_k"] == 25
    assert rag["wikilink_weight"] == 2.0
    assert rag["backlink_weight"] == 1.5
    # A partial update never resets the fields it did not mention.
    assert rag["tag_weight"] == 0.6
    assert rag["graph_weight"] == 0.4
    assert rag["chunk_target_tokens"] == 800
    assert rag["embedding_provider"] == "hash"


def test_enabled_link_path_reports_a_truthful_marker(tmp_path: Path) -> None:
    """The status marker is owned by the retriever, never derived by the service.

    When the retriever exposes ``link_retrieval`` it must say ``enabled`` for a
    wired, reachable path; when the attribute is absent the field degrades to
    ``""`` and the UI renders nothing rather than inventing a state.
    """
    app, _ = _workspace(tmp_path, link_retrieval_enabled=True)
    with TestClient(app) as client:
        client.headers[SESSION_HEADER] = app.state.runtime.session_id
        status = client.get("/api/v1/rag/index/status").json()
        stack = app.state.runtime.lifecycle.rag_stack
        assert stack.retriever.link_enabled is True
        marker = getattr(stack.retriever, "link_retrieval", None)
    if marker is not None:
        assert marker == "enabled"
    assert status["link_retrieval"] == str(marker or "")
    assert isinstance(status["link_retrieval"], str)


def test_link_retriever_degrades_without_an_index_instead_of_hiding(tmp_path: Path) -> None:
    """Enabled with no derived index: wired, degrading, never silently absent.

    Handing ``link=None`` would make the status card claim "nothing configured"
    and hide a real misconfiguration; opening a *second* derived index inside the
    RAG layer is forbidden (read-only invariant), so the retriever is wired with
    ``index=None`` and reports ``link_unavailable`` on use.
    """
    app, _ = _workspace(tmp_path, link_retrieval_enabled=True)
    from server.rag.factory import build_link_retriever

    with TestClient(app) as client:
        client.headers[SESSION_HEADER] = app.state.runtime.session_id
        stack = app.state.runtime.lifecycle.rag_stack
        assert stack is not None
        degraded = build_link_retriever(
            stack.store, None, app.state.runtime.settings.rag
        )
        assert degraded is not None
        assert degraded.retrieve("内容", top_k=5) == []
        assert degraded.last_degraded == "link_unavailable"
        # Disabled wins over everything, even with a usable index.
        disabled = app.state.runtime.settings.rag.model_copy(
            update={"link_retrieval_enabled": False}
        )
        assert build_link_retriever(stack.store, object(), disabled) is None


def test_link_construction_failure_degrades_instead_of_breaking_rag(
    tmp_path: Path, monkeypatch
) -> None:
    """A broken link path must never stop the RAG stack from being created."""
    app, _ = _workspace(tmp_path, link_retrieval_enabled=True)
    from server.rag import factory as factory_module

    def boom(*_args, **_kwargs):
        raise RuntimeError("link retriever exploded")

    with TestClient(app) as client:
        client.headers[SESSION_HEADER] = app.state.runtime.session_id
        stack = app.state.runtime.lifecycle.rag_stack
        monkeypatch.setattr(factory_module, "LinkRetriever", boom)
        assert (
            factory_module.build_link_retriever(
                stack.store, None, app.state.runtime.settings.rag
            )
            is None
        )
        # RAG keeps answering; only the optional path is gone.
        status = client.get("/api/v1/rag/index/status")
        assert status.status_code == 200
        assert stack.retriever.link_enabled is True  # built before the failure


def test_link_settings_reject_out_of_range_values(tmp_path: Path) -> None:
    app, _ = _workspace(tmp_path)
    with TestClient(app) as client:
        client.headers[SESSION_HEADER] = app.state.runtime.session_id
        revision = client.get("/api/v1/settings").json()["revision"]
        for payload in (
            {"wikilink_weight": 5.5},
            {"backlink_weight": -0.5},
            {"tag_weight": 6.0},
            {"graph_weight": -1},
            {"link_top_k": 0},
            {"link_top_k": 201},
        ):
            response = client.patch(
                "/api/v1/settings/rag",
                json={"expected_revision": revision, "rag": payload},
            )
            assert response.status_code == 422, (payload, response.text)
        # The bounds themselves stay accepted, and nothing was half-applied.
        accepted = client.patch(
            "/api/v1/settings/rag",
            json={
                "expected_revision": revision,
                "rag": {"link_top_k": 200, "graph_weight": 5.0},
            },
        )
        assert accepted.status_code == 200, accepted.text
        rag = accepted.json()["rag"]
        assert rag["link_top_k"] == 200
        assert rag["graph_weight"] == 5.0
        assert rag["wikilink_weight"] == 1.0


def test_patch_rejects_the_status_only_link_retrieval_key(tmp_path: Path) -> None:
    """``link_retrieval`` is a status field; PATCH must keep rejecting it."""
    app, _ = _workspace(tmp_path)
    with TestClient(app) as client:
        client.headers[SESSION_HEADER] = app.state.runtime.session_id
        revision = client.get("/api/v1/settings").json()["revision"]
        response = client.patch(
            "/api/v1/settings/rag",
            json={"expected_revision": revision, "rag": {"link_retrieval": "enabled"}},
        )
    assert response.status_code == 422


def _ts_interface_keys(name: str) -> set[str]:
    body = WEB_SETTINGS_TS.read_text(encoding="utf-8").split(
        f"export interface {name} {{", 1
    )[1].split("\n}", 1)[0]
    return set(re.findall(r"(\w+)\s*[?:]", body))


def _ts_patch_keys() -> set[str]:
    block = WEB_SETTINGS_TS.read_text(encoding="utf-8").split(
        "export const RAG_PATCH_KEYS = [", 1
    )[1].split("] as const", 1)[0]
    return set(re.findall(r'"(\w+)"', block))


def test_web_and_server_rag_settings_contract() -> None:
    """Fails on drift in either direction, so a silent key loss is impossible.

    The web type models the *snapshot* (wider than PATCH: response-side flags and
    env-only knobs), so equality is asserted where it belongs — between the PATCH
    whitelist and the server's declared PATCH model — plus containment in both
    directions for the type itself.
    """
    from server.api.routes.settings import RagConfiguration
    from server.config import RagSettings

    server_patch = set(RagConfiguration.model_fields)
    server_snapshot = set(RagSettings().snapshot_view())
    web_keys = _ts_interface_keys("RagConfiguration")
    web_patch = _ts_patch_keys()

    # (c) the wire whitelist is exactly what the server accepts: a stale list
    # would silently drop user input, an extra key would 422 every save.
    assert web_patch == server_patch, (
        f"missing on the wire: {sorted(server_patch - web_patch)}; "
        f"not accepted by the server: {sorted(web_patch - server_patch)}"
    )
    # (a) every accepted key is representable in the web type. The write-only
    # secret lives on RagConfigurationPatch instead of the snapshot type.
    assert server_patch - web_keys <= {"embedding_api_key"}
    # (b) the web type invents nothing the server never sends.
    assert web_keys <= server_snapshot, sorted(web_keys - server_snapshot)
    # (d) status-only keys stay out of both the type and the wire.
    assert "link_retrieval" not in web_keys
    assert "link_retrieval" not in web_patch
    # (e) env-only knobs stay a documented boundary until they get real inputs.
    for env_only in ("embedding_batch_size", "embedding_timeout_seconds", "use_env_proxy"):
        assert env_only not in web_keys
