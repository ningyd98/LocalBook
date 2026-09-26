import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from server.vault.errors import InvalidOperation
from server.vault.service import VaultService
from server.versions import VersionStore


def make_store(tmp_path: Path, *, timeout: float = 30.0) -> tuple[VaultService, VersionStore]:
    vault = VaultService(tmp_path, watcher_enabled=False, initialize=True)
    return vault, VersionStore(vault, timeout=timeout)


def test_versions_are_opt_in_and_exclude_derived_data(tmp_path: Path):
    vault, store = make_store(tmp_path)
    vault.create_bytes("note.md", b"one")
    assert store.status()["enabled"] is False
    assert not (tmp_path / ".localnote" / "versions").exists()
    store.enable()
    (tmp_path / ".localnote" / "derived.json").write_text("private")
    snapshot = store.snapshot()
    assert snapshot is not None
    assert store.history()["total"] == 1
    assert (
        "derived.json"
        not in store._run(
            ["--git-dir", str(store.repo), "ls-tree", "-r", "--name-only", "HEAD"]
        ).splitlines()
    )


def test_snapshot_history_content_diff_and_idempotence(tmp_path: Path):
    vault, store = make_store(tmp_path)
    vault.create_bytes("note.md", b"one")
    first = store.snapshot()
    assert first is not None
    assert store.snapshot() is None
    digest = store.history()["items"][0]["id"]
    current_digest = vault.read_bytes("note.md")[1]
    vault.write_bytes("note.md", b"two", expected_sha256=current_digest)
    second = store.snapshot()
    assert second is not None and second["id"] != digest
    assert store.content(digest, "note.md") == b"one"
    assert "two" in store.diff(digest, second["id"], "note.md")


def test_version_paths_reject_reserved_and_traversal(tmp_path: Path):
    _vault, store = make_store(tmp_path)
    store.enable()
    with pytest.raises(Exception):
        store.history(path="../secret")
    with pytest.raises(InvalidOperation):
        store.content("a" * 40, ".localnote/config.json")
    (tmp_path / "target.md").write_text("target")
    (tmp_path / "link.md").symlink_to(tmp_path / "target.md")
    with pytest.raises(InvalidOperation):
        store.history(path="link.md")


def test_parallel_snapshots_are_serialized(tmp_path: Path):
    vault, store = make_store(tmp_path)
    vault.create_bytes("note.md", b"one")
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: store.snapshot(), range(2)))
    assert sum(result is not None for result in outcomes) == 1
    assert store.history()["total"] == 1
    assert (tmp_path / "note.md").read_bytes() == b"one"


def test_git_failure_cleans_staging_and_preserves_documents(tmp_path: Path, monkeypatch):
    from server.vault.errors import VaultUnavailable

    vault, store = make_store(tmp_path)
    vault.create_bytes("note.md", b"safe")
    store.enable()
    original = store._run

    def fail_commit(args, **kwargs):
        if "commit" in args:
            raise VaultUnavailable("injected")
        return original(args, **kwargs)

    monkeypatch.setattr(store, "_run", fail_commit)
    with pytest.raises(VaultUnavailable):
        store.snapshot()
    assert (tmp_path / "note.md").read_bytes() == b"safe"
    assert not list(store.root.glob("snapshot-*"))


def test_corrupt_repo_is_rejected(tmp_path: Path):
    from server.vault.errors import VaultUnavailable

    _vault, store = make_store(tmp_path)
    store.enable()
    (store.repo / "HEAD").unlink()
    with pytest.raises(VaultUnavailable):
        store.enable()


def test_git_output_limit(tmp_path: Path, monkeypatch):
    from server.vault.errors import VaultUnavailable

    _vault, store = make_store(tmp_path)
    monkeypatch.setattr("server.versions.store._MAX_CAPTURE", 128)
    with pytest.raises(VaultUnavailable):
        store._run(["-c", "core.pager=cat", "log", "--format=%s", "HEAD"])


def test_timeout_and_output_overflow_kill_git_shim(tmp_path: Path):
    from server.vault.errors import VaultUnavailable

    _vault, store = make_store(tmp_path, timeout=2.0)
    store.max_output = 128
    shim = tmp_path / "shim"
    shim.mkdir()
    git = shim / "git"
    git.write_text(
        '#!/bin/sh\nif [ "$1" = "sleep" ]; then exec /bin/sleep 5; else exec /usr/bin/yes x; fi\n'
    )
    git.chmod(0o755)
    import os

    original = os.environ.get("PATH", "")
    os.environ["PATH"] = f"{shim}:{original}"
    try:
        with pytest.raises(VaultUnavailable, match="output exceeded"):
            store._run(["overflow"])
        with pytest.raises(VaultUnavailable, match="timed out"):
            store._run(["sleep"])
    finally:
        os.environ["PATH"] = original


def test_pending_commit_journal_recovers_config(tmp_path: Path, monkeypatch):
    vault, store = make_store(tmp_path)
    vault.create_bytes("note.md", b"v1")
    store.snapshot()
    vault.write_bytes("note.md", b"v2", expected_sha256=vault.read_bytes("note.md")[1])
    original_replace = os.replace
    failed = False

    def fail_config_replace(src, dst):
        nonlocal failed
        if Path(dst) == store.config and not failed:
            failed = True
            raise OSError("injected config failure")
        return original_replace(src, dst)

    monkeypatch.setattr(os, "replace", fail_config_replace)
    with pytest.raises(OSError):
        store.snapshot()
    assert store.pending.exists()
    monkeypatch.setattr(os, "replace", original_replace)
    store.snapshot()
    assert not store.pending.exists()
    assert store.history()["total"] == 2
