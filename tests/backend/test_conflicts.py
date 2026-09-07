"""M1 §8.3 conflict and precondition matrix (service level).

Server-side rules under test:
- update/delete/move require a matching ``expected_sha256``; mismatch ⇒
  ``FileConflict`` and the file stays untouched; missing ⇒
  ``ExpectedHashRequired``;
- external modification between snapshot and rename ⇒ ``FileConflict``;
- create of an existing file ⇒ ``AlreadyExists``; move of missing source ⇒
  ``PathNotFound``; move source == destination ⇒ ``InvalidOperation``.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from server.vault.errors import (
    AlreadyExists,
    ExpectedHashRequired,
    FileConflict,
    InvalidOperation,
    PathNotFound,
    VaultErrorCode,
)
from server.vault.service import VaultService, sha256_bytes


def test_update_with_stale_hash_conflicts_and_preserves_file(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    service = vault_service_factory(root)
    service.create_bytes("doc.md", b"current\n")
    stale = sha256_bytes(b"old content\n")

    with pytest.raises(FileConflict) as caught:
        service.write_bytes("doc.md", b"overwrite\n", stale)
    assert caught.value.code == VaultErrorCode.FILE_CONFLICT
    data, digest = service.read_bytes("doc.md")
    assert data == b"current\n"
    assert digest == sha256_bytes(b"current\n")


def test_update_without_expected_hash_is_rejected(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    service = vault_service_factory(root)
    service.create_bytes("doc.md", b"current\n")
    with pytest.raises(ExpectedHashRequired):
        service.write_bytes("doc.md", b"clobber\n", None)
    data, _ = service.read_bytes("doc.md")
    assert data == b"current\n"


def test_delete_with_stale_hash_conflicts_and_keeps_file(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    service = vault_service_factory(root)
    service.create_bytes("doomed.md", b"still here\n")
    with pytest.raises(FileConflict):
        service.delete_file("doomed.md", sha256_bytes(b"something else\n"))
    data, _ = service.read_bytes("doomed.md")
    assert data == b"still here\n"


def test_delete_without_expected_hash_is_rejected(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    service = vault_service_factory(root)
    service.create_bytes("doomed.md", b"still here\n")
    with pytest.raises(ExpectedHashRequired):
        service.delete_file("doomed.md", None)
    assert (root / "doomed.md").exists()


def test_move_with_stale_source_hash_conflicts(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "dest").mkdir()
    service = vault_service_factory(root)
    service.create_bytes("src.md", b"content\n")
    with pytest.raises(FileConflict):
        service.move_file("src.md", "dest/src.md", sha256_bytes(b"other\n"))
    assert (root / "src.md").read_bytes() == b"content\n"
    assert not (root / "dest" / "src.md").exists()


def test_move_without_expected_hash_is_rejected(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "dest").mkdir()
    service = vault_service_factory(root)
    service.create_bytes("src.md", b"content\n")
    with pytest.raises(ExpectedHashRequired):
        service.move_file("src.md", "dest/src.md", None)
    assert (root / "src.md").exists()


def test_move_missing_source_is_not_found(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    service = vault_service_factory(root)
    with pytest.raises(PathNotFound):
        service.move_file("absent.md", "dest.md", sha256_bytes(b"x" * 64))


def test_move_source_equals_destination_is_invalid(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    service = vault_service_factory(root)
    service.create_bytes("same.md", b"content\n")
    digest = sha256_bytes(b"content\n")
    with pytest.raises(InvalidOperation):
        service.move_file("same.md", "same.md", digest)


def test_move_into_existing_destination_conflicts(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    service = vault_service_factory(root)
    service.create_bytes("src.md", b"source\n")
    service.create_bytes("dst.md", b"destination\n")
    digest = sha256_bytes(b"source\n")
    with pytest.raises(AlreadyExists):
        service.move_file("src.md", "dst.md", digest)
    assert (root / "src.md").read_bytes() == b"source\n"
    assert (root / "dst.md").read_bytes() == b"destination\n"


def test_external_modification_before_move_detected(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "dest").mkdir()
    service = vault_service_factory(root)
    service.create_bytes("src.md", b"original\n")
    digest = sha256_bytes(b"original\n")

    # External writer changes the source between read and move.
    (root / "src.md").write_bytes(b"external change\n")
    with pytest.raises(FileConflict):
        service.move_file("src.md", "dest/src.md", digest)
    assert (root / "src.md").read_bytes() == b"external change\n"


def test_concurrent_create_same_path_is_single_winner(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    import threading

    root = tmp_path / "vault"
    root.mkdir()
    service = vault_service_factory(root)
    barrier = threading.Barrier(2)
    wins: list[str] = []

    def creator(payload: bytes, label: str) -> None:
        barrier.wait()
        try:
            service.create_bytes("new.md", payload)
            wins.append(label)
        except AlreadyExists:
            pass

    first = threading.Thread(target=creator, args=(b"A\n", "A"))
    second = threading.Thread(target=creator, args=(b"B\n", "B"))
    first.start()
    second.start()
    first.join(timeout=10)
    second.join(timeout=10)
    assert len(wins) == 1
    data, _ = service.read_bytes("new.md")
    assert data == (b"A\n" if wins[0] == "A" else b"B\n")


def test_delete_wrong_hash_is_not_silent_data_loss(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    """A hash that matches nothing still must not remove the file."""
    root = tmp_path / "vault"
    root.mkdir()
    service = vault_service_factory(root)
    service.create_bytes("keep.md", b"valuable\n")
    wrong = sha256_bytes(b"different bytes entirely")
    with pytest.raises(FileConflict):
        service.delete_file("keep.md", wrong)
    assert (root / "keep.md").read_bytes() == b"valuable\n"
