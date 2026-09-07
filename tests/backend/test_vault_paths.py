"""M1 §8.1 path-safety matrix: traversal, absolute, NUL, symlink escapes.

Every scenario runs against a throwaway root under ``tmp_path``; a real user
Vault is never touched.  The policy under test is conservative: all user
reachable symbolic links are rejected, including links that point back inside
the root.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from server.vault.errors import (
    FileConflict,
    InvalidOperation,
    InvalidRequest,
    NotADirectory,
    NotAFile,
    PathNotFound,
    PathTraversalError,
    SymlinkEscapeError,
    VaultErrorCode,
    VaultUnavailable,
)
from server.vault.service import VaultService


def _write(path: Path, data: bytes = b"secret outside content\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _make_service(
    root: Path,
    vault_service_factory: Callable[..., VaultService],
) -> VaultService:
    return vault_service_factory(root, initialize=False)


@pytest.mark.parametrize(
    "bad_path",
    [
        "../outside.md",
        "nested/../../x",
        "..",
        "a/..",
        "a/../b.md",
        "/tmp/x.md",
        "//host/share.md",
        "C:/x.md",
        "C:relative.md",
        r"\host\share.md",
        "a\\b.md",
        "a\x00b.md",
        "./x.md",
        "a//b.md",
    ],
)
def test_lexical_rejections(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
    bad_path: str,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    service = _make_service(root, vault_service_factory)
    with pytest.raises((PathTraversalError, InvalidRequest)):
        service.read_bytes(bad_path)
    with pytest.raises((PathTraversalError, InvalidRequest)):
        service.create_bytes(bad_path, b"data")


def test_traversal_and_backslash_are_path_traversal_code(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    service = _make_service(root, vault_service_factory)
    with pytest.raises(PathTraversalError) as caught:
        service.read_bytes("../outside.md")
    assert caught.value.code == VaultErrorCode.PATH_TRAVERSAL
    with pytest.raises(PathTraversalError) as caught2:
        service.read_bytes("a\\b.md")
    assert caught2.value.code == VaultErrorCode.PATH_TRAVERSAL


def test_nul_is_invalid_request(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    service = _make_service(root, vault_service_factory)
    with pytest.raises(InvalidRequest) as caught:
        service.read_bytes("a\x00b.md")
    assert caught.value.code == VaultErrorCode.INVALID_REQUEST


def test_not_found_and_type_errors(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "nested").mkdir()
    (root / "nested" / "note.md").write_text("# n\n", encoding="utf-8")
    (root / "plain.txt").write_text("file\n", encoding="utf-8")
    service = _make_service(root, vault_service_factory)

    with pytest.raises(PathNotFound):
        service.read_bytes("missing.md")
    with pytest.raises(PathNotFound):
        service.read_bytes("nested/missing.md")
    with pytest.raises(PathNotFound):
        service.read_bytes("no-such-dir/x.md")
    with pytest.raises(NotADirectory):
        service.create_bytes("plain.txt/child.md", b"x")
    with pytest.raises(NotAFile):
        service.read_bytes("nested")


def _build_escape_fixture(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "vault"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    _write(outside / "secret.md")
    _write(outside / "in-dir.md")
    _write(outside / "victim.md")

    (root / "escape-file.md").symlink_to(outside / "secret.md")
    (root / "escape-dir").symlink_to(outside, target_is_directory=True)
    chain = root / "chain"
    chain.mkdir()
    (chain / "linkparent").symlink_to(outside, target_is_directory=True)
    (root / "internal-link.md").symlink_to(root / "real.md")
    return root, outside


def test_symlink_file_escape_all_operations_denied(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root, outside = _build_escape_fixture(tmp_path)
    (root / "real.md").write_text("# real\n", encoding="utf-8")
    service = _make_service(root, vault_service_factory)

    with pytest.raises(SymlinkEscapeError):
        service.read_bytes("escape-file.md")
    with pytest.raises(SymlinkEscapeError):
        service.delete_file("escape-file.md", None)
    with pytest.raises(SymlinkEscapeError):
        service.move_file("escape-file.md", "moved.md", None)
    with pytest.raises(SymlinkEscapeError):
        service.create_bytes("escape-file.md", b"clobber")
    with pytest.raises(SymlinkEscapeError):
        service.list_tree()
    # The outside file was never read, written, deleted or moved.
    assert (outside / "secret.md").read_bytes() == b"secret outside content\n"


def test_symlink_directory_escape_denied(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root, outside = _build_escape_fixture(tmp_path)
    service = _make_service(root, vault_service_factory)

    with pytest.raises(SymlinkEscapeError):
        service.read_bytes("escape-dir/in-dir.md")
    with pytest.raises(SymlinkEscapeError):
        service.create_bytes("escape-dir/new.md", b"x")
    with pytest.raises(SymlinkEscapeError):
        service.move_file("escape-dir/in-dir.md", "out.md", None)
    with pytest.raises(SymlinkEscapeError):
        service.delete_file("escape-dir/in-dir.md", None)
    assert (outside / "in-dir.md").read_bytes() == b"secret outside content\n"


def test_symlink_parent_chain_escape_denied(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root, outside = _build_escape_fixture(tmp_path)
    service = _make_service(root, vault_service_factory)

    with pytest.raises(SymlinkEscapeError):
        service.read_bytes("chain/linkparent/victim.md")
    with pytest.raises(SymlinkEscapeError):
        service.create_bytes("chain/linkparent/created.md", b"x")
    with pytest.raises(SymlinkEscapeError):
        service.delete_file("chain/linkparent/victim.md", None)
    assert (outside / "victim.md").read_bytes() == b"secret outside content\n"


def test_internal_symlink_is_also_rejected(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root, _outside = _build_escape_fixture(tmp_path)
    (root / "real.md").write_text("# real\n", encoding="utf-8")
    service = _make_service(root, vault_service_factory)

    # Conservative policy: even a link whose target stays inside the root is
    # refused (PLAN-M1 §7 "最安全策略是拒绝所有 symlink").
    with pytest.raises(SymlinkEscapeError):
        service.read_bytes("internal-link.md")
    # The real file itself is readable when reached without the symlink.
    content, _digest = service.read_bytes("real.md")
    assert content == b"# real\n"


def test_real_file_still_readable_and_escape_marker_isolated(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root, outside = _build_escape_fixture(tmp_path)
    service = _make_service(root, vault_service_factory)
    (root / "real.md").write_text("# real\n", encoding="utf-8")
    # A listing raises loudly when the visible tree contains any symlink
    # (conservative policy), so exercise the safe path in a clean subdir.
    clean = root / "clean"
    clean.mkdir()
    (clean / "ok.md").write_text("# ok\n", encoding="utf-8")
    names = {entry.path for entry in service.list_tree("clean")}
    assert names == {"clean/ok.md"}
    assert (outside / "secret.md").read_bytes() == b"secret outside content\n"


def test_toctou_hook_external_rewrite_detected(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    state = {"rewritten": False}

    def external_rewrite_hook(path: Path) -> None:
        # Simulate an external process replacing the target between our
        # snapshot and the atomic rename.
        path.write_bytes(b"external writer won\n")
        state["rewritten"] = True

    service = vault_service_factory(
        root,
        initialize=False,
        before_replace_hook=external_rewrite_hook,
    )
    service.create_bytes("doc.md", b"original\n")
    _content, digest = service.read_bytes("doc.md")

    with pytest.raises(FileConflict):
        service.write_bytes("doc.md", b"overwrite attempt\n", digest)
    # The external content survives; the attempted write never replaced it.
    assert (root / "doc.md").read_bytes() == b"external writer won\n"
    assert state["rewritten"] is True
    leftovers = [p.name for p in root.iterdir() if "localnote-tmp" in p.name]
    assert leftovers == []


def test_toctou_hook_symlink_swap_never_writes_outside(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    _write(outside / "target.md")

    def symlink_swap_hook(path: Path) -> None:
        path.unlink()
        path.symlink_to(outside / "target.md")

    service = vault_service_factory(
        root,
        initialize=False,
        before_replace_hook=symlink_swap_hook,
    )
    service.create_bytes("doc.md", b"original\n")
    _content, digest = service.read_bytes("doc.md")

    with pytest.raises(SymlinkEscapeError):
        service.write_bytes("doc.md", b"should never land outside\n", digest)
    # The outside sentinel was never overwritten.
    assert (outside / "target.md").read_bytes() == b"secret outside content\n"
    assert (root / "doc.md").is_symlink()


def test_root_must_exist_and_be_directory() -> None:
    with pytest.raises(VaultUnavailable):
        VaultService("/definitely/not/a/real/root", watcher_enabled=False)


def test_missing_configured_root_is_unavailable_not_auto_created(
    tmp_path: Path,
    vault_service_factory: Callable[..., VaultService],
) -> None:
    missing = tmp_path / "missing-root"
    with pytest.raises(VaultUnavailable):
        vault_service_factory(missing, initialize=True)
    assert not missing.exists()


def test_reserved_localnote_rejected_by_public_api(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    service = vault_service_factory(root)
    with pytest.raises(InvalidOperation):
        service.list_tree(".localnote")
    with pytest.raises(InvalidOperation):
        service.create_bytes(".localnote/evil.md", b"x")
    with pytest.raises(InvalidOperation):
        service.read_bytes(".localnote/state.json")
    entries = service.list_tree(include_hidden=True)
    assert all(".localnote" not in entry.path for entry in entries)
