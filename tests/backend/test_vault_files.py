"""M1 §8.2 file-content and operation matrix (service level).

Runs against a copy of ``tests/fixtures/vault`` or fresh throwaway roots in
``tmp_path``.  Directory creation is allowed directly inside these throwaway
roots because it is fixture setup, not a public Vault API.
"""

from __future__ import annotations

import base64
import hashlib
import unicodedata
from collections.abc import Callable
from pathlib import Path

import pytest

from server.vault.errors import AlreadyExists, FileTooLarge, PathNotFound
from server.vault.service import VaultService, sha256_bytes


def _mkdirs(root: Path, relative: str) -> None:
    (root / relative).mkdir(parents=True, exist_ok=True)


def test_create_read_list_roundtrip_chinese_and_emoji(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    _mkdirs(root, "中文")
    service = vault_service_factory(root)

    relative = "中文/😀 note.md"
    content = "# 中文笔记 😀 你好\n\n> [!note] callout\n".encode()
    created = service.create_bytes(relative, content)
    assert created["operation"] == "created"
    assert created["path"] == relative
    assert created["sha256"] == sha256_bytes(content)
    assert created["byte_length"] == len(content)

    data, digest = service.read_bytes(relative)
    assert data == content
    assert digest == sha256_bytes(content)

    paths = {entry.path for entry in service.list_tree()}
    assert relative in paths
    assert "中文" in paths


def test_unicode_names_are_not_normalized(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    service = vault_service_factory(root)

    nfd_name = unicodedata.normalize("NFD", "café.md")
    nfc_name = unicodedata.normalize("NFC", "café.md")
    assert nfd_name != nfc_name
    service.create_bytes(nfd_name, b"nfd content\n")
    try:
        service.create_bytes(nfc_name, b"nfc content\n")
    except AlreadyExists:
        # Normalization-folding filesystem (HFS+/some mounts): the two names
        # collapse; verify the surviving entry is still byte-exact.
        data, _ = service.read_bytes(nfd_name)
        assert data in (b"nfd content\n", b"nfc content\n")
        return

    nfd_back, _ = service.read_bytes(nfd_name)
    nfc_back, _ = service.read_bytes(nfc_name)
    assert nfd_back == b"nfd content\n"
    assert nfc_back == b"nfc content\n"


def test_fixture_tree_listed_and_readable(
    vault_fixture_copy: Path,
    vault_service_factory: Callable[..., VaultService],
) -> None:
    root = vault_fixture_copy
    service = vault_service_factory(root)
    entries = service.list_tree()
    paths = {entry.path for entry in entries}

    assert "中文与 Unicode/😀 note.md" in paths
    assert "nested/with space.md" in paths
    assert "attachments/image.png" in paths
    assert "attachments" in paths
    assert "nested" in paths
    assert "large.md" in paths
    # The derived directory never leaks into listings.
    assert not any(".localnote" in path for path in paths)

    data, digest = service.read_bytes("中文与 Unicode/😀 note.md")
    expected = (root / "中文与 Unicode/😀 note.md").read_bytes()
    assert data == expected
    assert digest == f"sha256:{hashlib.sha256(expected).hexdigest()}"


def test_attachment_bytes_roundtrip(
    vault_fixture_copy: Path,
    vault_service_factory: Callable[..., VaultService],
) -> None:
    root = vault_fixture_copy
    service = vault_service_factory(root)
    original = (root / "attachments" / "image.png").read_bytes()
    data, digest = service.read_bytes("attachments/image.png")
    assert data == original
    assert digest == sha256_bytes(original)
    assert len(original) == 70


def test_duplicate_titles_are_independent_files(
    vault_fixture_copy: Path,
    vault_service_factory: Callable[..., VaultService],
) -> None:
    service = vault_service_factory(vault_fixture_copy)
    a, _ = service.read_bytes("duplicate-title-a.md")
    b, _ = service.read_bytes("duplicate-title-b.md")
    assert a != b
    assert b"Duplicate Title" in a and b"Duplicate Title" in b
    paths = {entry.path for entry in service.list_tree()}
    assert {"duplicate-title-a.md", "duplicate-title-b.md"} <= paths


def test_controlled_large_file_hash_and_roundtrip(
    vault_fixture_copy: Path,
    vault_service_factory: Callable[..., VaultService],
) -> None:
    root = vault_fixture_copy
    service = vault_service_factory(root)
    data, digest = service.read_bytes("large.md")
    assert data == (root / "large.md").read_bytes()
    assert len(data) > 200_000
    assert digest == sha256_bytes(data)
    entries = {entry.path: entry for entry in service.list_tree()}
    assert entries["large.md"].size == len(data)
    assert entries["large.md"].sha256 == digest


def test_over_limit_file_rejected(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "small.bin").write_bytes(b"x" * 32)
    (root / "big.bin").write_bytes(b"y" * 1024)
    service = vault_service_factory(root, max_file_bytes=64)

    with pytest.raises(FileTooLarge):
        service.read_bytes("big.bin")
    with pytest.raises(FileTooLarge):
        service.create_bytes("huge.md", b"z" * 65)
    # Over-limit files still list (without a digest) instead of failing the
    # whole tree.
    entries = {entry.path: entry for entry in service.list_tree()}
    assert entries["big.bin"].size == 1024
    assert entries["big.bin"].sha256 is None
    assert entries["small.bin"].sha256 is not None


def test_hidden_files_follow_include_hidden_but_localnote_never(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / ".hidden.md").write_text("# hidden\n", encoding="utf-8")
    (root / "visible.md").write_text("# visible\n", encoding="utf-8")
    service = vault_service_factory(root)  # initialize creates .localnote

    visible_paths = {entry.path for entry in service.list_tree()}
    assert visible_paths == {"visible.md"}
    assert ".hidden.md" not in visible_paths

    with_hidden = {entry.path for entry in service.list_tree(include_hidden=True)}
    assert ".hidden.md" in with_hidden
    assert ".localnote" not in with_hidden
    assert not any(path.startswith(".localnote/") for path in with_hidden)


def test_listing_modes_recursive_and_flat(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    _mkdirs(root, "x")
    service = vault_service_factory(root)
    service.create_bytes("x/leaf.md", b"# leaf\n")
    service.create_bytes("top.md", b"# top\n")

    flat = {entry.path for entry in service.list_tree("", recursive=False)}
    assert flat == {"x", "top.md"}

    recursive = {entry.path for entry in service.list_tree(recursive=True)}
    assert {"x", "x/leaf.md", "top.md"} <= recursive

    inside = {entry.path for entry in service.list_tree("x", recursive=False)}
    assert inside == {"x/leaf.md"}


def test_listing_sorted_by_unicode_code_point(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    service = vault_service_factory(root)
    names = ["b.md", "a.md", "Z.md", "中文.md", "😀.md"]
    for name in names:
        service.create_bytes(name, b"# x\n")
    got = [entry.path for entry in service.list_tree() if entry.kind == "file"]
    assert got == sorted(names)


def test_empty_vault_lists_empty(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    service = vault_service_factory(root)
    assert service.list_tree() == []


def test_create_duplicate_is_already_exists(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    service = vault_service_factory(root)
    service.create_bytes("a.md", b"one\n")
    with pytest.raises(AlreadyExists):
        service.create_bytes("a.md", b"two\n")
    data, _ = service.read_bytes("a.md")
    assert data == b"one\n"


def test_create_requires_existing_parent_directory(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    service = vault_service_factory(root)
    # PLAN-M1 §5.2: parent directories must already exist; no recursive mkdir.
    with pytest.raises(PathNotFound):
        service.create_bytes("deep/missing/leaf.md", b"x")
    assert not (root / "deep").exists()


def test_delete_then_read_is_not_found(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    service = vault_service_factory(root)
    service.create_bytes("gone.md", b"temporary\n")
    _data, digest = service.read_bytes("gone.md")
    result = service.delete_file("gone.md", digest)
    assert result["operation"] == "deleted"
    with pytest.raises(PathNotFound):
        service.read_bytes("gone.md")


def test_move_rename_happy_path_and_destination_conflict(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    _mkdirs(root, "archive")
    service = vault_service_factory(root)
    service.create_bytes("notes.md", b"# note\n")
    _data, digest = service.read_bytes("notes.md")

    moved = service.move_file("notes.md", "archive/notes.md", digest)
    assert moved["operation"] == "moved"
    assert moved["path"] == "archive/notes.md"
    assert moved["sha256"] == digest
    with pytest.raises(PathNotFound):
        service.read_bytes("notes.md")
    data, _ = service.read_bytes("archive/notes.md")
    assert data == b"# note\n"

    # Destination already exists → already_exists (409 semantics), source is
    # left untouched.
    service.create_bytes("archive/occupied.md", b"taken\n")
    _data2, digest2 = service.read_bytes("archive/notes.md")
    with pytest.raises(AlreadyExists):
        service.move_file("archive/notes.md", "archive/occupied.md", digest2)
    assert (root / "archive" / "occupied.md").read_bytes() == b"taken\n"
    data3, digest3 = service.read_bytes("archive/notes.md")
    assert data3 == b"# note\n"
    assert digest3 == digest2


def test_read_returns_metadata_and_content_type(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    service = vault_service_factory(root)
    payload = b"hello vault\n"
    service.create_bytes("readme.md", payload)
    info = service.read_file("readme.md")
    assert info["content"] == payload
    assert info["content_base64"] == base64.b64encode(payload).decode("ascii")
    assert info["byte_length"] == len(payload)
    assert info["sha256"] == sha256_bytes(payload)
    assert info["content_type"] == "text/markdown"

    service.create_bytes("photo.png", b"\x89PNG\r\n\x1a\nfakepng")
    image = service.read_file("photo.png")
    assert image["content_type"] == "image/png"
    assert image["byte_length"] == 15
