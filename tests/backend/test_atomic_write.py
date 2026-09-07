"""M1 §8.3 atomic write primitives: temp+fsync+atomic rename, cleanup, modes.

The service is the only production entry point for file I/O; these tests
exercise the observable guarantees of the atomic path through it.
"""

from __future__ import annotations

import stat
from collections.abc import Callable
from pathlib import Path

import pytest

from server.vault.errors import AlreadyExists, FileConflict
from server.vault.service import VaultService, sha256_bytes


def _temp_leftovers(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir() if "localnote-tmp" in p.name)


def test_create_then_update_is_atomic_and_byte_exact(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    service = vault_service_factory(root)

    service.create_bytes("note.md", b"version one\n")
    _data, digest_one = service.read_bytes("note.md")

    updated = service.write_bytes("note.md", b"version two\n", digest_one)
    assert updated["operation"] == "updated"
    assert updated["sha256"] == sha256_bytes(b"version two\n")
    data, digest_two = service.read_bytes("note.md")
    assert data == b"version two\n"
    assert digest_two != digest_one
    assert _temp_leftovers(root) == []


def test_no_temp_files_left_after_create_or_update(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    service = vault_service_factory(root)
    service.create_bytes("a.md", b"aaa\n")
    service.create_bytes("b.md", b"bbb\n")
    _data, digest = service.read_bytes("a.md")
    service.write_bytes("a.md", b"changed\n", digest)
    service.delete_file("b.md", sha256_bytes(b"bbb\n"))
    assert _temp_leftovers(root) == []


def test_empty_file_create_and_update(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    service = vault_service_factory(root)
    service.create_bytes("empty.md", b"")
    data, digest = service.read_bytes("empty.md")
    assert data == b""
    service.write_bytes("empty.md", b"now full\n", digest)
    data, _ = service.read_bytes("empty.md")
    assert data == b"now full\n"


def test_created_and_updated_files_are_private_by_default(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    service = vault_service_factory(root)
    service.create_bytes("private.md", b"secret\n")
    mode = (root / "private.md").stat().st_mode
    assert stat.S_IMODE(mode) & 0o077 == 0  # no group/other permissions

    _data, digest = service.read_bytes("private.md")
    service.write_bytes("private.md", b"still secret\n", digest)
    mode_after = (root / "private.md").stat().st_mode
    assert stat.S_IMODE(mode_after) & 0o077 == 0


def test_failed_update_preserves_original_and_cleans_temp(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()

    def failing_hook(_path: Path) -> None:
        raise RuntimeError("simulated crash between temp write and rename")

    service = vault_service_factory(
        root,
        initialize=False,
        before_replace_hook=failing_hook,
    )
    service.create_bytes("doc.md", b"original\n")
    _data, digest = service.read_bytes("doc.md")

    with pytest.raises(RuntimeError, match="simulated crash"):
        service.write_bytes("doc.md", b"replacement\n", digest)
    # Original bytes/hash survive and no temporary file is left behind.
    data, digest_after = service.read_bytes("doc.md")
    assert data == b"original\n"
    assert digest_after == digest
    assert _temp_leftovers(root) == []


def test_conflict_mid_write_cleans_temp_and_keeps_external_bytes(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()

    def external_modification_hook(path: Path) -> None:
        path.write_bytes(b"external version\n")

    service = vault_service_factory(
        root,
        initialize=False,
        before_replace_hook=external_modification_hook,
    )
    service.create_bytes("doc.md", b"original\n")
    _data, digest = service.read_bytes("doc.md")

    with pytest.raises(FileConflict):
        service.write_bytes("doc.md", b"should lose\n", digest)
    data, _ = service.read_bytes("doc.md")
    assert data == b"external version\n"
    assert _temp_leftovers(root) == []


def test_concurrent_updates_one_wins_one_conflicts(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    import threading

    root = tmp_path / "vault"
    root.mkdir()
    service = vault_service_factory(root)
    service.create_bytes("race.md", b"base\n")
    _data, expected = service.read_bytes("race.md")

    outcomes: list[str] = []
    barrier = threading.Barrier(2)

    def writer(payload: bytes, label: str) -> None:
        barrier.wait()
        try:
            service.write_bytes("race.md", payload, expected)
            outcomes.append(label)
        except FileConflict:
            pass

    first = threading.Thread(target=writer, args=(b"writer A\n", "A"))
    second = threading.Thread(target=writer, args=(b"writer B\n", "B"))
    first.start()
    second.start()
    first.join(timeout=10)
    second.join(timeout=10)
    assert not first.is_alive() and not second.is_alive()

    assert len(outcomes) == 1  # exactly one writer succeeded
    winner = outcomes[0]
    data, _ = service.read_bytes("race.md")
    assert data == (b"writer A\n" if winner == "A" else b"writer B\n")
    assert _temp_leftovers(root) == []


def test_create_never_overwrites_existing_file(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    service = vault_service_factory(root)
    service.create_bytes("doc.md", b"first\n")
    with pytest.raises(AlreadyExists):
        service.create_bytes("doc.md", b"second\n")
    data, _ = service.read_bytes("doc.md")
    assert data == b"first\n"
