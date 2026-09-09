"""POST /api/v1/vault/directory — folder creation contracts.

Parents must already exist (same rule as file creation), an existing target is
a conflict, and the path never escapes the vault. The response reuses the
FileMutationResponse shape with ``operation="created"``.
"""

from __future__ import annotations

from pathlib import Path

from tests.backend.client import TestClient

from server.vault.service import VaultService


def _client(vault_fixture_copy: Path, vault_service_factory: object, vault_api_client: object) -> TestClient:
    service: VaultService = vault_service_factory(vault_fixture_copy)  # type: ignore[call-arg]
    return vault_api_client(service=service)  # type: ignore[call-arg]


def test_create_directory_returns_created(
    vault_fixture_copy: Path, vault_service_factory: object, vault_api_client: object
) -> None:
    client = _client(vault_fixture_copy, vault_service_factory, vault_api_client)
    response = client.post("/api/v1/vault/directory", json={"path": "projects"})
    assert response.status_code == 201, response.text
    assert response.json() == {
        "path": "projects",
        "sha256": None,
        "byte_length": None,
        "operation": "created",
    }
    assert (vault_fixture_copy / "projects").is_dir()


def test_create_directory_inside_existing_folder(
    vault_fixture_copy: Path, vault_service_factory: object, vault_api_client: object
) -> None:
    client = _client(vault_fixture_copy, vault_service_factory, vault_api_client)
    assert client.post("/api/v1/vault/directory", json={"path": "attachments"}).status_code == 409
    created = client.post("/api/v1/vault/directory", json={"path": "attachments/2026"})
    assert created.status_code == 201, created.text
    assert (vault_fixture_copy / "attachments" / "2026").is_dir()


def test_create_directory_rejects_existing_directory(
    vault_fixture_copy: Path, vault_service_factory: object, vault_api_client: object
) -> None:
    client = _client(vault_fixture_copy, vault_service_factory, vault_api_client)
    client.post("/api/v1/vault/directory", json={"path": "projects"})
    again = client.post("/api/v1/vault/directory", json={"path": "projects"})
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "already_exists"


def test_create_directory_rejects_existing_file(
    vault_fixture_copy: Path, vault_service_factory: object, vault_api_client: object
) -> None:
    client = _client(vault_fixture_copy, vault_service_factory, vault_api_client)
    conflict = client.post("/api/v1/vault/directory", json={"path": "attachments/image.png"})
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "already_exists"


def test_create_directory_requires_existing_parent(
    vault_fixture_copy: Path, vault_service_factory: object, vault_api_client: object
) -> None:
    client = _client(vault_fixture_copy, vault_service_factory, vault_api_client)
    response = client.post("/api/v1/vault/directory", json={"path": "missing/child"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_create_directory_rejects_traversal(
    vault_fixture_copy: Path, vault_service_factory: object, vault_api_client: object
) -> None:
    client = _client(vault_fixture_copy, vault_service_factory, vault_api_client)
    for path in ("../escape", "/abs", "a/../../b"):
        response = client.post("/api/v1/vault/directory", json={"path": path})
        assert response.status_code == 400, path
        assert response.json()["error"]["code"] == "path_traversal"


def test_create_directory_ignores_hidden_derived_names(
    vault_fixture_copy: Path, vault_service_factory: object, vault_api_client: object
) -> None:
    """``.localnote`` is reserved for derived state and must not be creatable."""
    client = _client(vault_fixture_copy, vault_service_factory, vault_api_client)
    response = client.post("/api/v1/vault/directory", json={"path": ".localnote/sub"})
    assert response.status_code in (400, 409)
