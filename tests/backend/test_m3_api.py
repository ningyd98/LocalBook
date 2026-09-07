"""M3 REST integration matrix (PLAN-M3 §5.7/§6.1/§9.1).

Happy paths run against the *real* app lifespan (Vault → Index → Watcher
wiring) on a fixture copy, plus dependency-overridden clients for error
isolation.  Error bodies must keep the M1 contract and never leak the root.
"""

from __future__ import annotations

import json
import urllib.parse
from collections.abc import Callable
from pathlib import Path

from fastapi.testclient import TestClient

from server.api import dependencies
from server.api.main import create_app
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
    assert "File \"" not in text


def _configured_client(
    vault_fixture_copy: Path,
) -> TestClient:
    """Client whose app runs the full lifespan over a fixture copy."""
    from server.config import Settings, VaultSettings

    settings = Settings(vault=VaultSettings(root=vault_fixture_copy, watcher_enabled=False))
    app = create_app(settings=settings)
    return TestClient(app)


def _override_m3(app: object, vault: VaultService, index: DerivedIndexService) -> None:
    from server.links.service import LinksService
    from server.metadata.service import MetadataService
    from server.search.service import SearchService

    overrides = {
        dependencies.get_vault_service: lambda: vault,
        dependencies.get_index_service: lambda: index,
        dependencies.get_metadata_service: lambda: MetadataService(vault),
        dependencies.get_links_service: lambda: LinksService(index),
        dependencies.get_search_service: lambda: SearchService(index),
    }
    for dependency, provider in overrides.items():
        app.dependency_overrides[dependency] = provider  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Full-lifespan happy paths (Vault → Index → Watcher wiring)
# ---------------------------------------------------------------------------


def test_metadata_endpoint_path_and_query_forms(
    vault_fixture_copy: Path,
    vault_service_factory: Callable[..., VaultService],
) -> None:
    with _configured_client(vault_fixture_copy) as client:
        response = client.get(f"/api/v1/metadata/{_seg('中文 note.md')}")
        assert response.status_code == 200
        payload = response.json()
        assert payload["path"] == "中文 note.md"
        assert payload["frontmatter_status"] == "ok"
        assert payload["tags"] == ["中文标签", "工作"]
        assert payload["properties"]["emoji"] == "😀"
        assert payload["parse_error"] is None
        assert payload["available"] is True
        assert set(payload) == {
            "path",
            "title",
            "frontmatter_status",
            "properties",
            "tags",
            "parse_error",
            "available",
        }
        _assert_no_leaks(response.text, vault_fixture_copy)

        # query form is an equivalent entry point
        query_response = client.get(
            "/api/v1/metadata",
            params={"path": "frontmatter/unknown-fields.md"},
        )
        assert query_response.status_code == 200
        assert query_response.json()["frontmatter_status"] == "ok"
        assert query_response.json()["parse_error"] is None


def test_links_and_backlinks_over_lifespan(
    vault_fixture_copy: Path,
) -> None:
    with _configured_client(vault_fixture_copy) as client:
        links = client.get(f"/api/v1/links/{_seg('notes/a.md')}")
        assert links.status_code == 200
        payload = links.json()
        assert payload["path"] == "notes/a.md"
        assert payload["broken_count"] == 1
        by_target = {item["target"]: item for item in payload["outgoing"]}
        assert by_target["Ref A"]["resolved_path"] == "notes/Ref A.md"
        assert by_target["Cfg B"]["broken"] is True
        assert by_target["Alpha"]["ambiguous"] is True
        assert by_target["Alpha"]["candidates"] == ["dup/Alpha.md", "notes/Alpha.md"]

        back = client.get(f"/api/v1/backlinks/{_seg('notes/Ref A.md')}")
        assert back.status_code == 200
        back_payload = back.json()
        assert back_payload["count"] == 1
        assert back_payload["backlinks"][0]["source_path"] == "notes/a.md"
        assert "[[Ref A]]" in (back_payload["backlinks"][0]["text"] or "")


def test_search_and_rebuild_over_lifespan(
    vault_fixture_copy: Path,
) -> None:
    with _configured_client(vault_fixture_copy) as client:
        search = client.get("/api/v1/search", params={"q": "你好 世界"})
        assert search.status_code == 200
        payload = search.json()
        assert payload["query"] == "你好 世界"
        assert payload["total"] >= 1
        assert payload["skipped_notes"] >= 1  # non-utf8 note degraded
        assert payload["degraded"] is True
        _assert_no_leaks(search.text, vault_fixture_copy)

        rebuild = client.post("/api/v1/index/rebuild")
        assert rebuild.status_code == 200
        rebuild_payload = rebuild.json()
        assert rebuild_payload["ready"] is True
        assert rebuild_payload["indexed"] > 0
        assert isinstance(rebuild_payload["duration_ms"], float)
        assert set(rebuild_payload) == {
            "indexed",
            "skipped",
            "failed",
            "duration_ms",
            "ready",
            "generated_at",
        }

        # a note created through the API is searchable after a rebuild
        created = client.post(
            "/api/v1/vault/file",
            json={
                "path": "notes/new-note.md",
                "content_base64": (
                    "IyBOZXcKbmV3LXRlcm0tYWJjLTEyMyBib2R5Lgo="
                ),
            },
        )
        assert created.status_code == 201
        client.post("/api/v1/index/rebuild")
        found = client.get("/api/v1/search", params={"q": "new-term-abc-123"})
        assert found.status_code == 200
        paths = [hit["path"] for hit in found.json()["hits"]]
        assert "notes/new-note.md" in paths
        # clean up the note again so fixture copies stay byte-stable
        data = client.get("/api/v1/vault/file", params={"path": "notes/new-note.md"})
        digest = data.json()["sha256"]
        deleted = client.request(
            "DELETE",
            "/api/v1/vault/file",
            json={"path": "notes/new-note.md", "expected_sha256": digest},
        )
        assert deleted.status_code == 200


def test_parse_failure_is_http_200_not_an_error(
    vault_fixture_copy: Path,
) -> None:
    with _configured_client(vault_fixture_copy) as client:
        bad = client.get(f"/api/v1/metadata/{_seg('frontmatter/bad-yaml.md')}")
        assert bad.status_code == 200
        assert bad.json()["frontmatter_status"] == "parse_error"
        assert bad.json()["parse_error"]["kind"] == "yaml"

        unterminated = client.get(
            f"/api/v1/metadata/{_seg('frontmatter/unterminated.md')}"
        )
        assert unterminated.status_code == 200
        assert unterminated.json()["parse_error"]["kind"] == "unterminated"

        non_utf8 = client.get(f"/api/v1/metadata/{_seg('bytes/non-utf8.md')}")
        assert non_utf8.status_code == 200
        assert non_utf8.json()["frontmatter_status"] == "unreadable"
        assert non_utf8.json()["parse_error"]["kind"] == "decode"


# ---------------------------------------------------------------------------
# Errors and isolation
# ---------------------------------------------------------------------------


def test_unconfigured_vault_is_503_for_m3_endpoints() -> None:
    client = TestClient(create_app(), raise_server_exceptions=False)
    for method, url, params in [
        ("get", "/api/v1/metadata/a.md", None),
        ("get", "/api/v1/links/a.md", None),
        ("get", "/api/v1/backlinks/a.md", None),
        ("get", "/api/v1/search", {"q": "x"}),
        ("post", "/api/v1/index/rebuild", None),
    ]:
        response = client.request(method.upper(), url, params=params)
        assert response.status_code == 503, (method, url)
        error = _error(response)
        assert error["code"] == "vault_not_configured"


def test_health_and_vault_untouched_by_index_unavailable(
    vault_fixture_copy: Path,
) -> None:
    with _configured_client(vault_fixture_copy) as client:
        index = client.app.state.index_service
        index.clear()  # simulate an unavailable index
        search = client.get("/api/v1/search", params={"q": "你好"})
        assert search.status_code == 503
        assert _error(search)["code"] == "index_unavailable"
        links = client.get("/api/v1/links/notes/a.md")
        assert links.status_code == 503
        metadata = client.get(f"/api/v1/metadata/{_seg('中文 note.md')}")
        assert metadata.status_code == 503
        # Vault/health stay fine
        assert client.get("/api/v1/health").json() == {"status": "ok"}
        tree = client.get("/api/v1/vault/files")
        assert tree.status_code == 200
        # rebuild recovers the index
        rebuild = client.post("/api/v1/index/rebuild")
        assert rebuild.status_code == 200
        assert rebuild.json()["ready"] is True
        assert client.get("/api/v1/search", params={"q": "你好"}).status_code == 200


def test_missing_note_is_404_not_found(
    vault_fixture_copy: Path,
    vault_service_factory: Callable[..., VaultService],
    index_service_factory: Callable[..., DerivedIndexService],
) -> None:
    vault = vault_service_factory(vault_fixture_copy)  # type: ignore[arg-type]
    index = index_service_factory(vault)
    app = create_app()
    client = TestClient(app, raise_server_exceptions=False)
    _override_m3(app, vault, index)
    try:
        for url in (
            "/api/v1/metadata/nope.md",
            "/api/v1/links/nope.md",
            "/api/v1/backlinks/nope.md",
        ):
            response = client.get(url)
            assert response.status_code == 404
            assert _error(response)["code"] == "not_found"
    finally:
        app.dependency_overrides.clear()


def test_invalid_search_queries_are_400(
    vault_fixture_copy: Path,
) -> None:
    with _configured_client(vault_fixture_copy) as client:
        for query in ("", "   ", "a\u0000b", "x" * 300):
            response = client.get("/api/v1/search", params={"q": query})
            assert response.status_code == 400
            assert _error(response)["code"] == "invalid_request"


def test_error_bodies_never_leak_roots_or_unicode_paths(
    vault_fixture_copy: Path,
) -> None:
    with _configured_client(vault_fixture_copy) as client:
        response = client.get("/api/v1/metadata/nope.md")
        text = response.text
        _assert_no_leaks(text, vault_fixture_copy)
        payload = json.loads(text)
        assert payload["error"]["path"] in (None, "nope.md")
