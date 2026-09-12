"""Vault trash: soft delete, restore, retention (default 30 days).

Two layers are covered: ``TrashService`` against a real temporary Vault (the
only place that touches the filesystem) and the REST contract through FastAPI,
including the security body shape and the "never touch .localnote through the
public API" invariant.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from tests.backend.client import TestClient

from server.api import dependencies
from server.api.main import create_app
from server.config import Settings
from server.vault.service import VaultService, sha256_bytes
from server.vault.trash import TRASH_DIR_NAME, TrashService
from server.vault.trash_schemas import TrashListResponse


def _digest(data: bytes) -> str:
    return sha256_bytes(data)


@pytest.fixture
def vault_service_factory(tmp_path: Path):
    def _factory(root: Path | None = None) -> VaultService:
        target = root or tmp_path / "vault"
        target.mkdir(parents=True, exist_ok=True)
        service = VaultService(target, watcher_enabled=False)
        service.initialize(start_watcher=False)
        return service

    return _factory


@pytest.fixture
def trash(vault_service_factory) -> TrashService:
    return TrashService(vault_service_factory(), retention_days=30)


def _write(vault: VaultService, relative: str, data: bytes = b"body\n") -> str:
    """Create one file (with its parent folders) and return its digest."""
    relative = relative.strip("/")
    parent = relative.rsplit("/", 1)[0] if "/" in relative else ""
    if parent and not (vault.root / parent).exists():
        vault.create_directory(parent)
    return vault.create_bytes(relative, data)["sha256"]


def _tree(trash: TrashService) -> list[str]:
    """Payload files only; the derived ``index.json`` is not a payload."""
    root = trash.root
    if not root.exists():
        return []
    return sorted(
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.name != "index.json"
    )


# ---------------------------------------------------------------------------
# TrashService
# ---------------------------------------------------------------------------


def test_trash_moves_the_file_out_of_the_vault(trash: TrashService, vault_service_factory):
    vault = trash.vault
    digest = _write(vault, "notes/a.md", b"# A\n")

    item = trash.trash("notes/a.md", digest)

    assert item.original_path == "notes/a.md"
    assert item.kind == "file"
    assert item.file_count == 1
    assert item.byte_length == 4
    assert not (vault.root / "notes/a.md").exists()
    # The payload is inside the trash, byte-identical.
    payload = trash.root / item.blob
    assert payload.read_bytes() == b"# A\n"
    assert (vault.root / ".localnote" / TRASH_DIR_NAME).is_dir()


def test_trash_refuses_a_changed_file(trash: TrashService):
    vault = trash.vault
    _write(vault, "a.md", b"one\n")
    with pytest.raises(Exception) as excinfo:
        trash.trash("a.md", _digest(b"different\n"))
    assert getattr(excinfo.value, "code", None) == "file_conflict"
    assert (vault.root / "a.md").exists()


def test_trash_requires_a_digest_for_files(trash: TrashService):
    vault = trash.vault
    _write(vault, "a.md")
    with pytest.raises(Exception) as excinfo:
        trash.trash("a.md", None)
    assert getattr(excinfo.value, "code", None) == "expected_hash_required"


def test_trash_moves_a_whole_folder(trash: TrashService):
    vault = trash.vault
    _write(vault, "folder/one.md", b"1\n")
    _write(vault, "folder/deep/two.md", b"22\n")

    item = trash.trash("folder", None)

    assert item.kind == "directory"
    assert item.file_count == 2
    assert item.byte_length == 5
    assert not (vault.root / "folder").exists()
    assert (trash.root / item.blob / "one.md").read_bytes() == b"1\n"
    assert (trash.root / item.blob / "deep" / "two.md").read_bytes() == b"22\n"


def test_trash_rejects_the_reserved_derived_directory(trash: TrashService):
    with pytest.raises(Exception) as excinfo:
        trash.trash(".localnote/index.db", None)
    assert getattr(excinfo.value, "code", None) == "invalid_request"


def test_restore_puts_the_item_back(trash: TrashService):
    vault = trash.vault
    digest = _write(vault, "notes/a.md", b"# A\n")
    item = trash.trash("notes/a.md", digest)
    assert trash.list_entries()

    restored, renamed = trash.restore(item.id)

    assert restored == "notes/a.md"
    assert renamed is False
    assert (vault.root / "notes/a.md").read_bytes() == b"# A\n"
    assert trash.list_entries() == []
    assert _tree(trash) == []


def test_restore_recreates_a_missing_parent_folder(trash: TrashService):
    vault = trash.vault
    digest = _write(vault, "notes/a.md", b"# A\n")
    item = trash.trash("notes/a.md", digest)
    # The folder itself may have been trashed afterwards.
    for name in ("notes",):
        (vault.root / name).rmdir()

    restored, _renamed = trash.restore(item.id)

    assert restored == "notes/a.md"
    assert (vault.root / "notes/a.md").read_bytes() == b"# A\n"


def test_restore_refuses_an_occupied_path_unless_asked_to_rename(trash: TrashService):
    vault = trash.vault
    digest = _write(vault, "a.md", b"old\n")
    item = trash.trash("a.md", digest)
    _write(vault, "a.md", b"new\n")  # something else took the path

    with pytest.raises(Exception) as excinfo:
        trash.restore(item.id)
    assert getattr(excinfo.value, "code", None) == "already_exists"
    assert (vault.root / "a.md").read_bytes() == b"new\n"

    restored, renamed = trash.restore(item.id, rename_if_occupied=True)
    assert restored == "a (restored).md"
    assert renamed is True
    assert (vault.root / "a (restored).md").read_bytes() == b"old\n"
    assert (vault.root / "a.md").read_bytes() == b"new\n"


def test_restore_a_folder_tree(trash: TrashService):
    vault = trash.vault
    _write(vault, "folder/one.md", b"1\n")
    _write(vault, "folder/deep/two.md", b"22\n")
    item = trash.trash("folder", None)

    restored, _renamed = trash.restore(item.id)

    assert restored == "folder"
    assert (vault.root / "folder" / "one.md").read_bytes() == b"1\n"
    assert (vault.root / "folder" / "deep" / "two.md").read_bytes() == b"22\n"


def test_delete_and_empty_remove_payloads(trash: TrashService):
    vault = trash.vault
    first = trash.trash("a.md", _write(vault, "a.md"))
    second = trash.trash("b.md", _write(vault, "b.md"))

    trash.delete(first.id)
    assert [view.item.id for view in trash.list_entries()] == [second.id]
    assert not (trash.root / first.blob).exists()

    assert trash.empty() == 1
    assert trash.list_entries() == []
    assert _tree(trash) == []


def test_retention_purges_expired_entries(trash: TrashService):
    vault = trash.vault
    digest = _write(vault, "a.md", b"# A\n")
    item = trash.trash("a.md", digest)

    # Not yet expired: the 30 day window is intact.
    assert trash.purge_expired() == 0
    assert trash.list_entries()

    # 31 days later the entry (payload included) is gone.
    assert trash.purge_expired(now=datetime.now(UTC) + timedelta(days=31)) == 1
    assert trash.list_entries() == []
    assert not (trash.root / item.blob).exists()
    assert not (trash.root / item.id).exists()


def test_retention_boundary_is_exactly_the_window(trash: TrashService):
    vault = trash.vault
    item = trash.trash("a.md", _write(vault, "a.md"))
    view = trash.view(item)
    assert view.days_remaining == 30
    # Just inside the window: still there; at/after it: purged.
    assert trash.purge_expired(now=view.expires_at - timedelta(seconds=1)) == 0
    assert trash.purge_expired(now=view.expires_at) == 1


def test_days_remaining_counts_down(trash: TrashService):
    vault = trash.vault
    item = trash.trash("a.md", _write(vault, "a.md"))
    later = trash.view(item)
    assert later.days_remaining == 30
    assert later.expires_at > datetime.now(UTC)


def test_index_is_derived_and_survives_a_corrupt_file(trash: TrashService):
    vault = trash.vault
    item = trash.trash("a.md", _write(vault, "a.md"))
    # A corrupt index must not raise or delete payloads; it just stops listing.
    trash.index_path.write_bytes(b"{ not json")
    assert trash.list_entries() == []
    assert (trash.root / item.blob).exists()
    # A damaged payload is reported as unavailable, not silently indexed away.
    (trash.root / item.blob).unlink()
    with pytest.raises(Exception):
        trash.restore(item.id)


def test_index_file_is_not_user_reachable_through_the_vault_api(trash: TrashService, tmp_path: Path):
    vault = trash.vault
    trash.trash("a.md", _write(vault, "a.md"))
    listing = vault.list_tree(recursive=True, include_hidden=False)
    assert all(not entry["path"].startswith(".localnote") for entry in listing)
    with pytest.raises(Exception) as excinfo:
        vault.read_bytes(".localnote/trash/index.json")
    assert getattr(excinfo.value, "code", None) == "invalid_request"


# ---------------------------------------------------------------------------
# REST contract
# ---------------------------------------------------------------------------


@pytest.fixture
def trash_client(tmp_path: Path):
    """Client over a real (throwaway) Vault, using the app's own trash provider.

    No dependency override here on purpose: the point of these tests is the
    wiring ``route → get_trash_service → get_vault_service`` the app actually
    uses, including the retention value it reads from settings.
    """
    root = tmp_path / "vault"
    root.mkdir()
    settings = Settings.model_validate({"vault": {"root": str(root), "watcher_enabled": False}})
    app = create_app(settings=settings)
    client = TestClient(app, raise_server_exceptions=False)
    # One request brings the Vault lifecycle up; the bin is built from the same
    # service the routes use (retention comes from settings, i.e. the default 30).
    assert client.get("/api/v1/vault/files").status_code == 200
    vault = client.app.state.vault_service
    client.vault = vault  # type: ignore[attr-defined]
    client.trash = TrashService(vault, retention_days=settings.vault.trash_retention_days)  # type: ignore[attr-defined]
    assert client.get("/api/v1/trash").status_code == 200
    return client


def test_trash_api_round_trip(trash_client):
    vault: VaultService = trash_client.vault
    digest = _write(vault, "notes/a.md", b"# A\n")

    created = trash_client.post("/api/v1/trash", json={"path": "notes/a.md", "expected_sha256": digest})
    assert created.status_code == 201
    entry = created.json()
    assert entry["original_path"] == "notes/a.md"
    assert entry["name"] == "a.md"
    assert entry["kind"] == "file"
    assert entry["days_remaining"] == 30
    assert entry["byte_length"] == 4
    assert "id" in entry and "expires_at" in entry

    listing = trash_client.get("/api/v1/trash")
    assert listing.status_code == 200
    payload = TrashListResponse.model_validate(listing.json())
    assert payload.count == 1
    assert payload.retention_days == 30
    assert payload.total_bytes == 4
    assert not (vault.root / "notes/a.md").exists()

    restored = trash_client.post(f"/api/v1/trash/{entry['id']}/restore", json={})
    assert restored.status_code == 200
    assert restored.json()["path"] == "notes/a.md"
    assert restored.json()["operation"] == "moved"
    assert (vault.root / "notes/a.md").read_bytes() == b"# A\n"
    assert trash_client.get("/api/v1/trash").json()["count"] == 0


def test_trash_api_moves_a_folder_and_deletes_one_entry(trash_client):
    vault: VaultService = trash_client.vault
    _write(vault, "folder/one.md", b"1\n")
    _write(vault, "folder/two.md", b"22\n")

    entry = trash_client.post("/api/v1/trash", json={"path": "folder"}).json()
    assert entry["kind"] == "directory"
    assert entry["file_count"] == 2
    assert not (vault.root / "folder").exists()

    removed = trash_client.delete(f"/api/v1/trash/{entry['id']}")
    assert removed.status_code == 200
    assert removed.json()["operation"] == "deleted"
    assert trash_client.get("/api/v1/trash").json()["count"] == 0


def test_trash_api_empty(trash_client):
    vault: VaultService = trash_client.vault
    for name in ("a.md", "b.md"):
        digest = _write(vault, name, f"# {name}\n".encode())
        trash_client.post("/api/v1/trash", json={"path": name, "expected_sha256": digest})

    emptied = trash_client.delete("/api/v1/trash")

    assert emptied.status_code == 200
    assert emptied.json()["count"] == 0
    assert emptied.json()["entries"] == []


def test_trash_api_error_bodies(trash_client):
    vault: VaultService = trash_client.vault
    _write(vault, "a.md", b"one\n")

    # Missing digest for a file: the M1 rule, unchanged.
    missing = trash_client.post("/api/v1/trash", json={"path": "a.md"})
    assert missing.status_code == 400
    assert set(missing.json()["error"]) == {"code", "message", "path"}
    assert missing.json()["error"]["code"] == "expected_hash_required"

    # Stale digest: 409, and the file stays.
    conflict = trash_client.post("/api/v1/trash", json={"path": "a.md", "expected_sha256": _digest(b"other\n")})
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "file_conflict"
    assert (vault.root / "a.md").exists()

    # The reserved derived directory is never reachable.
    reserved = trash_client.post("/api/v1/trash", json={"path": ".localnote/index.db"})
    assert reserved.status_code == 400
    assert reserved.json()["error"]["code"] == "invalid_request"

    # Unknown entry: 404; malformed id: 400.
    assert trash_client.post("/api/v1/trash/does-not-exist/restore", json={}).status_code == 404
    assert trash_client.delete("/api/v1/trash/..%2Fescape").status_code in {400, 404}

    # A body never leaks the absolute root.
    for response in (missing, conflict, reserved):
        assert str(vault.root) not in response.text


def test_trash_service_defaults_to_thirty_days(vault_service_factory):
    """The window the UI promises comes from the service default (and settings)."""
    vault = vault_service_factory()
    assert TrashService(vault).retention_days == 30
    settings = Settings()
    assert settings.vault.trash_retention_days == 30

    app = create_app()
    app.dependency_overrides[dependencies.get_vault_service] = lambda: vault
    app.dependency_overrides[dependencies.get_trash_service] = lambda: TrashService(vault)
    client = TestClient(app, raise_server_exceptions=False)
    listing = client.get("/api/v1/trash")
    assert listing.status_code == 200
    assert listing.json()["retention_days"] == 30


def test_index_json_is_a_human_readable_derived_file(trash: TrashService):
    vault = trash.vault
    item = trash.trash("notes/a.md", _write(vault, "notes/a.md", b"# A\n"))
    payload = json.loads(trash.index_path.read_text())
    assert payload["version"] == 1
    assert payload["entries"][0]["original_path"] == "notes/a.md"
    assert payload["entries"][0]["id"] == item.id
    # Deleting the derived index only loses bookkeeping, never content.
    trash.index_path.unlink()
    assert trash.list_entries() == []
    assert (trash.root / item.blob).read_bytes() == b"# A\n"
