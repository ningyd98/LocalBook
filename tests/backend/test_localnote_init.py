"""M1 §8.4 ``.localnote`` initialization: dirs + state placeholder only.

No database, no schema, no FTS, no content copy may ever appear under
``.localnote``.  Deleting the whole directory and re-initializing must leave
every Markdown/attachment byte and hash untouched.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from server.vault.derived import (
    DERIVED_DIR_NAME,
    STATE_FILE_NAME,
    STATE_PAYLOAD,
    initialize_derived,
)
from server.vault.errors import SymlinkEscapeError, VaultUnavailable
from server.vault.service import VaultService, sha256_bytes


def _content_snapshot(service: VaultService, relative: str) -> tuple[bytes, str]:
    data, digest = service.read_bytes(relative)
    return data, digest


def test_initialize_creates_only_dir_and_state_placeholder(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    service = vault_service_factory(root)  # initialize=True
    assert service.initialized is True

    derived = root / DERIVED_DIR_NAME
    assert derived.is_dir()
    state = derived / STATE_FILE_NAME
    assert state.is_file()
    payload = json.loads(state.read_text(encoding="utf-8"))
    assert payload == STATE_PAYLOAD
    # Only the placeholder file exists: no *.db, *.sqlite, FTS or index files.
    names = sorted(p.name for p in derived.iterdir())
    assert names == [STATE_FILE_NAME]
    assert not any(name.endswith((".db", ".sqlite", ".sqlite3")) for name in names)
    # The placeholder payload contains no note content by construction.
    raw = state.read_bytes()
    assert b"#" not in raw  # no Markdown body copy inside


def test_initialize_is_idempotent(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    first = vault_service_factory(root)
    second = vault_service_factory(root)  # re-open on the same root
    assert first.initialized and second.initialized
    state = root / DERIVED_DIR_NAME / STATE_FILE_NAME
    assert json.loads(state.read_text(encoding="utf-8")) == STATE_PAYLOAD


def test_delete_localnote_and_recreate_keeps_content_hashes(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "attachments").mkdir()
    service = vault_service_factory(root)
    note_bytes = b"# durable note\nbody\r\nmixed \xff bytes\n"
    service.create_bytes("durable.md", note_bytes)
    (root / "attachments" / "image.png").write_bytes(b"\x89PNG\r\n\x1a\nimg")
    before = (
        _content_snapshot(service, "durable.md"),
        _content_snapshot(service, "attachments/image.png"),
    )

    # Simulate "delete derived data" — must not affect any content file.
    shutil.rmtree(root / DERIVED_DIR_NAME)
    assert not (root / DERIVED_DIR_NAME).exists()

    reopened = vault_service_factory(root)
    assert reopened.initialized is True
    assert (root / DERIVED_DIR_NAME / STATE_FILE_NAME).is_file()
    after = (
        _content_snapshot(reopened, "durable.md"),
        _content_snapshot(reopened, "attachments/image.png"),
    )
    assert after == before
    assert after[0][0] == note_bytes


def test_initialize_derived_helper_returns_derived_path() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        derived = initialize_derived(root)
        assert derived == root / DERIVED_DIR_NAME
        assert derived.is_dir()


def test_localnote_as_file_is_rejected(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / DERIVED_DIR_NAME).write_text("i am a file, not a dir\n", encoding="utf-8")
    with pytest.raises(VaultUnavailable):
        vault_service_factory(root)
    # The blocking file was not replaced or deleted.
    assert (root / DERIVED_DIR_NAME).read_text(encoding="utf-8").startswith("i am a file")


def test_localnote_as_symlink_is_rejected(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    target = tmp_path / "elsewhere"
    target.mkdir()
    (root / DERIVED_DIR_NAME).symlink_to(target, target_is_directory=True)
    with pytest.raises(SymlinkEscapeError):
        vault_service_factory(root)


def test_derived_state_file_symlink_is_rejected(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / DERIVED_DIR_NAME).mkdir()
    outside = tmp_path / "state-target.json"
    outside.write_text('{"hijack": true}\n', encoding="utf-8")
    (root / DERIVED_DIR_NAME / STATE_FILE_NAME).symlink_to(outside)
    with pytest.raises(SymlinkEscapeError):
        vault_service_factory(root)
    # Outside file untouched.
    assert outside.read_text(encoding="utf-8") == '{"hijack": true}\n'


def test_created_files_have_stable_hash_before_and_after_derived_rebuild(
    vault_fixture_copy: Path,
    vault_service_factory: Callable[..., VaultService],
) -> None:
    root = vault_fixture_copy
    first = vault_service_factory(root)
    before = {
        entry.path: entry.sha256
        for entry in first.list_tree()
        if entry.kind == "file"
    }
    assert len(before) >= 8
    shutil.rmtree(root / DERIVED_DIR_NAME)
    second = vault_service_factory(root)
    after = {
        entry.path: entry.sha256
        for entry in second.list_tree()
        if entry.kind == "file"
    }
    assert after == before
    # Sanity: fixture sha256 fields are present and stable.
    assert after["large.md"] == sha256_bytes((root / "large.md").read_bytes())


def test_index_db_is_created_by_index_layer_not_vault_init(
    vault_fixture_copy: Path,
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    """M4: Vault init still leaves only state.json; opening + rebuilding the
    DerivedIndexService creates the SQLite derived library inside .localnote,
    which remains deletable without touching any note."""
    from server.index.service import DerivedIndexService

    root = vault_fixture_copy
    service = vault_service_factory(root)
    derived = root / DERIVED_DIR_NAME
    assert sorted(p.name for p in derived.iterdir()) == [STATE_FILE_NAME]

    index = DerivedIndexService(service)
    try:
        result = index.rebuild()
        assert result.ready is True
        assert (derived / "index.db").is_file()
        assert (derived / STATE_FILE_NAME).is_file()
    finally:
        index.close()
    # after a clean close the WAL files are checkpointed away; state.json and
    # the derived db remain, and can be removed freely
    shutil.rmtree(derived)
    assert not derived.exists()
    assert _content_snapshot(service, "中文 note.md")[1].startswith("sha256:")
