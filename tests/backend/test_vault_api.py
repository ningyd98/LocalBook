"""M1 §8.5 FastAPI integration: DTOs, HTTP mapping, security bodies.

The VaultService is injected exclusively through FastAPI's dependency
override mechanism; routes never see a filesystem ``Path`` from user input.
Error responses never contain the absolute root, a traceback, file content
(other than the explicit read response) or extra fields.
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import pytest
from tests.backend.client import TestClient

from server.vault.service import VaultService, sha256_bytes

_READ_PATH = "中文与 Unicode/😀 note.md"


def _error(response: object) -> dict:
    payload = response.json()  # type: ignore[attr-defined]
    assert set(payload) == {"error"}
    assert set(payload["error"]) == {"code", "message", "path"}
    return payload["error"]


def _assert_no_leaks(text: str, root: Path) -> None:
    assert str(root.resolve()) not in text
    assert "Traceback" not in text
    assert "File \"" not in text


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


# --------------------------------------------------------------------------
# Not configured / unavailable
# --------------------------------------------------------------------------


def test_vault_unconfigured_returns_503_for_all_endpoints(
    vault_api_client: object,
) -> None:
    client: TestClient = vault_api_client()  # type: ignore[call-arg]
    for method, url, payload in [
        ("get", "/api/v1/vault/files", None),
        ("get", "/api/v1/vault/file?path=a.md", None),
        ("post", "/api/v1/vault/file", {"path": "a.md", "content_base64": _b64(b"x")}),
        ("patch", "/api/v1/vault/file", {"path": "a.md", "content_base64": _b64(b"x")}),
        ("delete", "/api/v1/vault/file", {"path": "a.md"}),
        ("post", "/api/v1/vault/file/move", {"source_path": "a.md", "destination_path": "b.md"}),
    ]:
        response = client.request(method.upper(), url, json=payload)
        assert response.status_code == 503, (method, url, response.text)
        error = _error(response)
        assert error["code"] == "vault_not_configured"
        assert error["path"] is None


def test_health_stays_ok_when_vault_unconfigured(client: TestClient) -> None:
    assert client.get("/api/v1/health").json() == {"status": "ok"}
    response = client.get("/api/v1/vault/files")
    assert response.status_code == 503
    assert _error(response)["code"] == "vault_not_configured"


def test_configured_but_missing_root_is_503_and_not_created(
    tmp_path: Path,
    vault_api_client: object,
    configured_settings: object,
) -> None:
    missing = tmp_path / "never-created-root"
    settings = configured_settings(missing)  # type: ignore[call-arg]
    client: TestClient = vault_api_client(settings=settings)  # type: ignore[call-arg]
    response = client.get("/api/v1/vault/files")
    assert response.status_code == 503
    error = _error(response)
    assert error["code"] == "vault_unavailable"
    assert not missing.exists()  # LocalNote never auto-creates a user root


# --------------------------------------------------------------------------
# Listing / reading
# --------------------------------------------------------------------------


def test_list_files_recursive_smoke(
    vault_fixture_copy: Path,
    vault_service_factory: object,
    vault_api_client: object,
) -> None:
    service: VaultService = vault_service_factory(vault_fixture_copy)  # type: ignore[call-arg]
    client: TestClient = vault_api_client(service=service)  # type: ignore[call-arg]
    response = client.get("/api/v1/vault/files")
    assert response.status_code == 200
    payload = response.json()
    assert payload["root"] == "."
    paths = {entry["path"] for entry in payload["entries"]}
    assert _READ_PATH in paths
    assert "attachments/image.png" in paths
    assert not any(".localnote" in path for path in paths)
    _assert_no_leaks(response.text, vault_fixture_copy)


def test_read_file_returns_base64_bytes_and_metadata(
    vault_fixture_copy: Path,
    vault_service_factory: object,
    vault_api_client: object,
) -> None:
    service: VaultService = vault_service_factory(vault_fixture_copy)  # type: ignore[call-arg]
    client: TestClient = vault_api_client(service=service)  # type: ignore[call-arg]
    expected = (vault_fixture_copy / _READ_PATH).read_bytes()

    response = client.get("/api/v1/vault/file", params={"path": _READ_PATH})
    assert response.status_code == 200
    payload = response.json()
    assert payload["path"] == _READ_PATH
    assert base64.b64decode(payload["content_base64"]) == expected
    assert payload["byte_length"] == len(expected)
    assert payload["sha256"] == f"sha256:{hashlib.sha256(expected).hexdigest()}"
    assert payload["content_type"] == "text/markdown"
    assert set(payload) == {
        "path",
        "content_base64",
        "byte_length",
        "sha256",
        "content_type",
    }
    _assert_no_leaks(response.text, vault_fixture_copy)


def test_read_attachment_content_type(
    vault_fixture_copy: Path,
    vault_service_factory: object,
    vault_api_client: object,
) -> None:
    service: VaultService = vault_service_factory(vault_fixture_copy)  # type: ignore[call-arg]
    client: TestClient = vault_api_client(service=service)  # type: ignore[call-arg]
    response = client.get("/api/v1/vault/file", params={"path": "attachments/image.png"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["content_type"] == "image/png"
    original = (vault_fixture_copy / "attachments" / "image.png").read_bytes()
    assert base64.b64decode(payload["content_base64"]) == original


def test_read_missing_file_is_404(
    vault_service_factory: object,
    vault_api_client: object,
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    service: VaultService = vault_service_factory(root)  # type: ignore[call-arg]
    client: TestClient = vault_api_client(service=service)  # type: ignore[call-arg]
    response = client.get("/api/v1/vault/file", params={"path": "missing.md"})
    assert response.status_code == 404
    assert _error(response)["code"] == "not_found"


# --------------------------------------------------------------------------
# Path-safety over HTTP
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_path",
    ["../outside.md", "nested/../../x.md", "/tmp/x.md", "C:/x.md", r"\share\f.md"],
)
def test_traversal_queries_return_400(
    bad_path: str,
    vault_service_factory: object,
    vault_api_client: object,
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    service: VaultService = vault_service_factory(root)  # type: ignore[call-arg]
    client: TestClient = vault_api_client(service=service)  # type: ignore[call-arg]
    response = client.get("/api/v1/vault/file", params={"path": bad_path})
    assert response.status_code == 400, response.text
    error = _error(response)
    assert error["code"] == "path_traversal"
    _assert_no_leaks(response.text, root)


def test_nul_and_reserved_queries_return_400(
    vault_service_factory: object,
    vault_api_client: object,
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    service: VaultService = vault_service_factory(root)  # type: ignore[call-arg]
    client: TestClient = vault_api_client(service=service)  # type: ignore[call-arg]

    nul = client.get("/api/v1/vault/file", params={"path": "a\x00b.md"})
    assert nul.status_code == 400
    assert _error(nul)["code"] == "invalid_request"

    reserved = client.get("/api/v1/vault/file", params={"path": ".localnote/state.json"})
    assert reserved.status_code == 400
    assert _error(reserved)["code"] == "invalid_request"


def test_symlink_escape_over_http(
    vault_service_factory: object,
    vault_api_client: object,
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("top secret\n", encoding="utf-8")
    (root / "link.md").symlink_to(outside / "secret.md")
    service: VaultService = vault_service_factory(root, initialize=False)  # type: ignore[call-arg]
    client: TestClient = vault_api_client(service=service)  # type: ignore[call-arg]

    for method, url, payload in [
        ("get", "/api/v1/vault/file?path=link.md", None),
        ("delete", "/api/v1/vault/file", {"path": "link.md"}),
        ("patch", "/api/v1/vault/file", {"path": "link.md", "content_base64": _b64(b"x")}),
    ]:
        response = client.request(method.upper(), url, json=payload)
        assert response.status_code == 400
        assert _error(response)["code"] == "symlink_escape"
    assert (outside / "secret.md").read_bytes() == b"top secret\n"


# --------------------------------------------------------------------------
# Create / update / delete / move
# --------------------------------------------------------------------------


def test_create_update_read_delete_lifecycle(
    vault_service_factory: object,
    vault_api_client: object,
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "notes").mkdir()
    service: VaultService = vault_service_factory(root)  # type: ignore[call-arg]
    client: TestClient = vault_api_client(service=service)  # type: ignore[call-arg]

    # create → 201
    first = b"# Hello\n\nfirst content\n"
    response = client.post(
        "/api/v1/vault/file",
        json={"path": "notes/new.md", "content_base64": _b64(first)},
    )
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["operation"] == "created"
    assert created["path"] == "notes/new.md"
    assert created["sha256"] == sha256_bytes(first)

    # duplicate create → 409
    duplicate = client.post(
        "/api/v1/vault/file",
        json={"path": "notes/new.md", "content_base64": _b64(b"second")},
    )
    assert duplicate.status_code == 409
    assert _error(duplicate)["code"] == "already_exists"

    # read to learn the current hash
    read = client.get("/api/v1/vault/file", params={"path": "notes/new.md"})
    current_hash = read.json()["sha256"]

    # update with correct hash → 200
    second = b"# Hello\n\nupdated content\n"
    updated = client.patch(
        "/api/v1/vault/file",
        json={
            "path": "notes/new.md",
            "content_base64": _b64(second),
            "expected_sha256": current_hash,
        },
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["operation"] == "updated"
    assert body["sha256"] == sha256_bytes(second)

    read_after = client.get("/api/v1/vault/file", params={"path": "notes/new.md"})
    assert base64.b64decode(read_after.json()["content_base64"]) == second

    # stale-hash update → 409 file_conflict
    stale = client.patch(
        "/api/v1/vault/file",
        json={
            "path": "notes/new.md",
            "content_base64": _b64(b"clobber"),
            "expected_sha256": current_hash,
        },
    )
    assert stale.status_code == 409
    assert _error(stale)["code"] == "file_conflict"

    # delete with correct hash → 200
    removed = client.request(
        "DELETE",
        "/api/v1/vault/file",
        json={"path": "notes/new.md", "expected_sha256": sha256_bytes(second)},
    )
    assert removed.status_code == 200
    assert removed.json()["operation"] == "deleted"
    gone = client.get("/api/v1/vault/file", params={"path": "notes/new.md"})
    assert gone.status_code == 404


def test_update_without_expected_hash_is_400(
    vault_service_factory: object,
    vault_api_client: object,
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    service: VaultService = vault_service_factory(root)  # type: ignore[call-arg]
    client: TestClient = vault_api_client(service=service)  # type: ignore[call-arg]
    client.post("/api/v1/vault/file", json={"path": "a.md", "content_base64": _b64(b"x")})

    response = client.patch(
        "/api/v1/vault/file",
        json={"path": "a.md", "content_base64": _b64(b"y")},  # no expected_sha256
    )
    assert response.status_code == 400
    assert _error(response)["code"] == "expected_hash_required"


def test_malformed_hash_and_base64_are_400_invalid_request(
    vault_service_factory: object,
    vault_api_client: object,
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    service: VaultService = vault_service_factory(root)  # type: ignore[call-arg]
    client: TestClient = vault_api_client(service=service)  # type: ignore[call-arg]
    client.post("/api/v1/vault/file", json={"path": "a.md", "content_base64": _b64(b"x")})

    bad_hash = client.patch(
        "/api/v1/vault/file",
        json={"path": "a.md", "content_base64": _b64(b"y"), "expected_sha256": "not-a-digest"},
    )
    assert bad_hash.status_code == 400
    assert _error(bad_hash)["code"] == "invalid_request"

    bad_b64 = client.post(
        "/api/v1/vault/file",
        json={"path": "b.md", "content_base64": "%%%not-base64%%%"},
    )
    assert bad_b64.status_code == 400
    assert _error(bad_b64)["code"] == "invalid_request"


def test_too_large_payload_is_413(
    vault_service_factory: object,
    vault_api_client: object,
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    service: VaultService = vault_service_factory(root, max_file_bytes=64)  # type: ignore[call-arg]
    client: TestClient = vault_api_client(service=service)  # type: ignore[call-arg]
    response = client.post(
        "/api/v1/vault/file",
        json={"path": "huge.md", "content_base64": _b64(b"z" * 4096)},
    )
    assert response.status_code == 413
    assert _error(response)["code"] == "file_too_large"


def test_move_and_rename_over_http(
    vault_service_factory: object,
    vault_api_client: object,
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "archive").mkdir()
    service: VaultService = vault_service_factory(root)  # type: ignore[call-arg]
    client: TestClient = vault_api_client(service=service)  # type: ignore[call-arg]
    client.post("/api/v1/vault/file", json={"path": "a.md", "content_base64": _b64(b"# a\n")})
    digest = client.get("/api/v1/vault/file", params={"path": "a.md"}).json()["sha256"]

    moved = client.post(
        "/api/v1/vault/file/move",
        json={
            "source_path": "a.md",
            "destination_path": "archive/a.md",
            "expected_sha256": digest,
        },
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["operation"] == "moved"
    assert moved.json()["path"] == "archive/a.md"

    # source gone, destination readable
    assert client.get("/api/v1/vault/file", params={"path": "a.md"}).status_code == 404
    content = client.get("/api/v1/vault/file", params={"path": "archive/a.md"})
    assert base64.b64decode(content.json()["content_base64"]) == b"# a\n"


def test_move_error_cases_over_http(
    vault_service_factory: object,
    vault_api_client: object,
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    service: VaultService = vault_service_factory(root)  # type: ignore[call-arg]
    client: TestClient = vault_api_client(service=service)  # type: ignore[call-arg]
    client.post("/api/v1/vault/file", json={"path": "src.md", "content_base64": _b64(b"# s\n")})
    client.post("/api/v1/vault/file", json={"path": "dst.md", "content_base64": _b64(b"# d\n")})
    digest = client.get("/api/v1/vault/file", params={"path": "src.md"}).json()["sha256"]
    stale = sha256_bytes(b"stale")

    # destination exists → 409 already_exists
    hit_dest = client.post(
        "/api/v1/vault/file/move",
        json={"source_path": "src.md", "destination_path": "dst.md", "expected_sha256": digest},
    )
    assert hit_dest.status_code == 409
    assert _error(hit_dest)["code"] == "already_exists"

    # stale source hash → 409 file_conflict
    stale_move = client.post(
        "/api/v1/vault/file/move",
        json={"source_path": "src.md", "destination_path": "gone.md", "expected_sha256": stale},
    )
    assert stale_move.status_code == 409
    assert _error(stale_move)["code"] == "file_conflict"

    # missing source → 404
    missing = client.post(
        "/api/v1/vault/file/move",
        json={"source_path": "nope.md", "destination_path": "x.md", "expected_sha256": digest},
    )
    assert missing.status_code == 404
    assert _error(missing)["code"] == "not_found"

    # traversal destination → 400 path_traversal
    traversal = client.post(
        "/api/v1/vault/file/move",
        json={"source_path": "src.md", "destination_path": "../out.md", "expected_sha256": digest},
    )
    assert traversal.status_code == 400
    assert _error(traversal)["code"] == "path_traversal"

    # missing expected hash → 400 expected_hash_required
    no_hash = client.post(
        "/api/v1/vault/file/move",
        json={"source_path": "src.md", "destination_path": "x.md"},
    )
    assert no_hash.status_code == 400
    assert _error(no_hash)["code"] == "expected_hash_required"


def test_delete_error_cases_over_http(
    vault_service_factory: object,
    vault_api_client: object,
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    service: VaultService = vault_service_factory(root)  # type: ignore[call-arg]
    client: TestClient = vault_api_client(service=service)  # type: ignore[call-arg]
    client.post("/api/v1/vault/file", json={"path": "doomed.md", "content_base64": _b64(b"x")})

    stale = client.request(
        "DELETE",
        "/api/v1/vault/file",
        json={"path": "doomed.md", "expected_sha256": sha256_bytes(b"y")},
    )
    assert stale.status_code == 409
    assert _error(stale)["code"] == "file_conflict"

    missing = client.request(
        "DELETE",
        "/api/v1/vault/file",
        json={"path": "absent.md", "expected_sha256": sha256_bytes(b"x")},
    )
    assert missing.status_code == 404

    no_hash = client.request("DELETE", "/api/v1/vault/file", json={"path": "doomed.md"})
    assert no_hash.status_code == 400
    assert _error(no_hash)["code"] == "expected_hash_required"


def test_create_into_missing_parent_is_404(
    vault_service_factory: object,
    vault_api_client: object,
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    service: VaultService = vault_service_factory(root)  # type: ignore[call-arg]
    client: TestClient = vault_api_client(service=service)  # type: ignore[call-arg]
    response = client.post(
        "/api/v1/vault/file",
        json={"path": "deep/missing/leaf.md", "content_base64": _b64(b"x")},
    )
    assert response.status_code == 404
    assert _error(response)["code"] == "not_found"


def test_list_respects_query_filters(
    vault_service_factory: object,
    vault_api_client: object,
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / ".hidden.md").write_text("# h\n", encoding="utf-8")
    service: VaultService = vault_service_factory(root)  # type: ignore[call-arg]
    client: TestClient = vault_api_client(service=service)  # type: ignore[call-arg]
    client.post("/api/v1/vault/file", json={"path": "seen.md", "content_base64": _b64(b"x")})

    visible = client.get("/api/v1/vault/files").json()
    visible_paths = {entry["path"] for entry in visible["entries"]}
    assert "seen.md" in visible_paths
    assert ".hidden.md" not in visible_paths
    assert not any(path.startswith(".localnote") for path in visible_paths)

    hidden = client.get("/api/v1/vault/files", params={"include_hidden": "true"}).json()
    hidden_paths = {entry["path"] for entry in hidden["entries"]}
    assert ".hidden.md" in hidden_paths
    assert not any(path.startswith(".localnote") for path in hidden_paths)
