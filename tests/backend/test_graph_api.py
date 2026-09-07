"""M5 REST API integration matrix (PLAN-M5 §5.3/§9.1 rows 7–9).

Happy paths run against the real app lifespan (Vault → Index → Watcher
wiring) on a fixture copy.  Error bodies keep the M1 contract
``{"error":{code,message,path}}`` and never leak the configured root.
Failure isolation: an unavailable index 503s the graph while health and
Vault file reads keep working.
"""

from __future__ import annotations

import urllib.parse
from collections.abc import Callable
from pathlib import Path

from fastapi.testclient import TestClient

from server.api import dependencies
from server.api.main import create_app
from server.graph.service import GraphService
from server.index.service import DerivedIndexService
from server.vault.service import VaultService


def _seg(path: str) -> str:
    """Percent-encode each path segment, leaving literal ``/`` separators."""
    return "/".join(urllib.parse.quote(segment, safe="") for segment in path.split("/"))


def _error(response: object) -> dict:
    payload = response.json()  # type: ignore[attr-defined]
    assert set(payload) == {"error"}
    assert set(payload["error"]) == {"code", "message", "path"}
    return payload["error"]


def _assert_no_leaks(text: str, root: Path) -> None:
    assert str(root.resolve()) not in text
    assert "Traceback" not in text
    assert 'File "' not in text


def _configured_client(vault_fixture_copy: Path) -> TestClient:
    from server.config import Settings, VaultSettings

    settings = Settings(vault=VaultSettings(root=vault_fixture_copy, watcher_enabled=False))
    return TestClient(create_app(settings=settings))


# ---------------------------------------------------------------------------
# Happy paths over the full lifespan
# ---------------------------------------------------------------------------


def test_global_graph_endpoint_shape(vault_fixture_copy: Path) -> None:
    with _configured_client(vault_fixture_copy) as client:
        response = client.get("/api/v1/graph?limit=2000")
        assert response.status_code == 200
        payload = response.json()
        assert payload["model"] == "note-tag-v1"
        assert payload["scope"] == "global"
        assert payload["root"] is None
        assert payload["page"]["total_nodes"] == 28
        assert payload["page"]["total_edges"] == 25
        assert payload["page"]["truncated"] is False
        # sorted deterministically: (type, id)
        ids = [node["id"] for node in payload["nodes"]]
        assert ids == sorted(ids)
        edge_keys = [(e["type"], e["source"], e["target"], e["id"]) for e in payload["edges"]]
        assert edge_keys == sorted(edge_keys)
        _assert_no_leaks(response.text, vault_fixture_copy)


def test_local_endpoint_with_chinese_path(vault_fixture_copy: Path) -> None:
    with _configured_client(vault_fixture_copy) as client:
        response = client.get(
            f"/api/v1/graph/local/{_seg('中文 note.md')}?depth=1&direction=both"
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["scope"] == "local"
        assert payload["root"] == "中文 note.md"
        assert any(n["type"] == "tag" and n["tag_folded"] == "工作" for n in payload["nodes"])
        assert any(n["type"] == "tag" and n["tag_folded"] == "中文标签" for n in payload["nodes"])

        # path with space + nested emoji folder
        emoji_response = client.get(
            f"/api/v1/graph/local/{_seg('中文与 Unicode/😀 note.md')}?depth=0"
        )
        assert emoji_response.status_code == 200
        emoji = emoji_response.json()
        assert emoji["root"] == "中文与 Unicode/😀 note.md"
        assert emoji["page"]["total_nodes"] == 1


def test_tag_endpoint_path_and_query_forms(vault_fixture_copy: Path) -> None:
    with _configured_client(vault_fixture_copy) as client:
        by_path = client.get(f"/api/v1/graph/tag/{_seg('工作')}")
        assert by_path.status_code == 200
        payload = by_path.json()
        assert payload["scope"] == "tag"
        assert payload["page"]["total_nodes"] == 3
        # casefold match
        alpha = client.get("/api/v1/graph/tag/ALPHA")
        assert alpha.status_code == 200
        assert alpha.json()["page"]["total_nodes"] == 2

        by_query = client.get("/api/v1/graph/tag", params={"tag": "工作"})
        assert by_query.status_code == 200
        by_path_payload = by_path.json()
        by_query_payload = by_query.json()
        by_path_payload.pop("generated_at")
        by_query_payload.pop("generated_at")
        assert by_query_payload == by_path_payload


def test_missing_note_and_missing_tag_are_404(vault_fixture_copy: Path) -> None:
    with _configured_client(vault_fixture_copy) as client:
        local = client.get(f"/api/v1/graph/local/{_seg('notes/never.md')}")
        assert local.status_code == 404
        error = _error(local)
        assert error["code"] == "not_found"
        assert error["path"] == "notes/never.md"

        tag = client.get(f"/api/v1/graph/tag/{_seg('从不存在的标签')}")
        assert tag.status_code == 404
        error = _error(tag)
        assert error["code"] == "not_found"
        assert error["path"] == "从不存在的标签"


def test_parameter_validation_is_400_invalid_request(vault_fixture_copy: Path) -> None:
    with _configured_client(vault_fixture_copy) as client:
        cases = [
            ("/api/v1/graph?limit=3000", "limit"),
            ("/api/v1/graph?offset=-1", "offset"),
            ("/api/v1/graph/local/notes/a.md?depth=9", "depth"),
            ("/api/v1/graph/local/notes/a.md?direction=up", "direction"),
            ("/api/v1/graph/tag?tag=%00", "control tag"),
        ]
        for url, label in cases:
            response = client.get(url)
            assert response.status_code == 400, f"{label}: {response.status_code}"
            error = _error(response)
            assert error["code"] == "invalid_request"
            assert response.text.count("Traceback") == 0


def test_include_broken_query_flag(vault_fixture_copy: Path) -> None:
    with _configured_client(vault_fixture_copy) as client:
        full = client.get("/api/v1/graph?limit=2000").json()
        without = client.get("/api/v1/graph?limit=2000&include_broken=false").json()
        assert full["page"]["total_edges"] == 25
        assert without["page"]["total_edges"] == 18
        assert all(edge["target"] for edge in without["edges"])


def test_pagination_mechanics_over_api(vault_fixture_copy: Path) -> None:
    with _configured_client(vault_fixture_copy) as client:
        page_one = client.get("/api/v1/graph?limit=10&offset=0").json()
        page_two = client.get("/api/v1/graph?limit=10&offset=10").json()
        page_three = client.get("/api/v1/graph?limit=10&offset=20").json()

        assert page_one["page"]["next_offset"] == 10
        assert page_one["page"]["truncated"] is True
        assert page_two["page"]["next_offset"] == 20
        assert page_three["page"]["next_offset"] is None
        assert page_three["page"]["truncated"] is False

        seen = {node["id"] for page in (page_one, page_two, page_three) for node in page["nodes"]}
        assert len(seen) == page_one["page"]["total_nodes"]
        # a truncated page never carries a normal edge to a node outside it
        for page in (page_one, page_two):
            page_ids = {node["id"] for node in page["nodes"]}
            for edge in page["edges"]:
                if edge["target"]:
                    assert edge["target"] in page_ids

        repeated = client.get("/api/v1/graph?limit=10&offset=10").json()
        repeated.pop("generated_at")
        page_two.pop("generated_at")
        assert repeated == page_two


def test_graph_503_isolation_health_and_vault_stay_up(
    vault_fixture_copy: Path,
    vault_service_factory: Callable[..., VaultService],
) -> None:
    from server.config import Settings

    app = create_app(Settings())
    client = TestClient(app, raise_server_exceptions=False)
    service = vault_service_factory(vault_fixture_copy)
    index = DerivedIndexService(service)
    index.rebuild()
    index.clear()  # readiness gone; db handle stays open, rows untouched

    app.dependency_overrides[dependencies.get_vault_service] = lambda: service
    app.dependency_overrides[dependencies.get_graph_service] = (
        lambda: GraphService(index)
    )
    try:
        graph = client.get("/api/v1/graph?limit=100")
        assert graph.status_code == 503
        error = _error(graph)
        assert error["code"] == "index_unavailable"
        _assert_no_leaks(graph.text, vault_fixture_copy)

        health = client.get("/api/v1/health")
        assert health.status_code == 200
        assert health.json() == {"status": "ok"}

        file_read = client.get(
            "/api/v1/vault/file",
            params={"path": "notes/a.md"},
        )
        assert file_read.status_code == 200
        assert file_read.json()["path"] == "notes/a.md"
    finally:
        app.dependency_overrides.clear()
        index.close()
