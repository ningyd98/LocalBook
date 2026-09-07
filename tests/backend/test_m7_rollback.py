"""M7 rollback tests (PLAN-M7 §9.1-5/7): executor inverses & conflicts."""

from __future__ import annotations

from pathlib import Path

import pytest

from server.actions.diff import sha256
from server.recovery.executor import TransactionExecutor
from server.recovery.schemas import JobStatus, TransactionOperation
from server.vault.service import VaultService

import m7support as m7

A = b"alpha\n"
B = b"beta\n"
C = b"gamma\n"


def _ops(vault: VaultService, files: dict[str, bytes]) -> list[TransactionOperation]:
    operations: list[TransactionOperation] = []
    for path, content in files.items():
        data, digest = vault.read_bytes(path)
        operations.append(
            TransactionOperation(
                operation="update",
                path=path,
                before_exists=True,
                before_bytes=data,
                before_hash=digest,
                after_bytes=content,
                after_hash=sha256(content),
            )
        )
    return operations


def test_rollback_conflict_when_external_edit_arrives(
    vault_service_factory: object, tmp_path: Path
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    injections: dict[int, bytes] = {}
    counter = {"n": 0}

    def hook(path: Path) -> None:
        counter["n"] += 1
        if counter["n"] in injections:
            path.write_bytes(injections[counter["n"]])

    service = vault_service_factory(  # type: ignore[call-arg]
        root, before_replace_hook=hook
    )
    service.create_bytes("f1.md", A)
    service.create_bytes("f2.md", B)
    # Writes during execution: f1->a2, f2->b2, then f1 rollback write (#3)
    # triggers the injected external edit on f1 -> rollback conflict.
    # f1's rollback write is the 4th write_bytes call (f1, f2, f2-rollback,
    # f1-rollback); inject the external edit exactly there.
    injections[4] = b"external-wins\n"
    operations = [
        TransactionOperation(
            operation="update",
            path="f1.md",
            before_exists=True,
            before_bytes=A,
            before_hash=sha256(A),
            after_bytes=b"a2\n",
            after_hash=sha256(b"a2\n"),
        ),
        TransactionOperation(
            operation="update",
            path="f2.md",
            before_exists=True,
            before_bytes=B,
            before_hash=sha256(B),
            after_bytes=b"b2\n",
            after_hash=sha256(b"b2\n"),
        ),
    ]
    history = m7.make_history(tmp_path)
    history.record(job_id="job-1", task_type="manual", status="executing")
    executor = TransactionExecutor(service, history=history)
    # Force a mid-transaction failure after f1/f2 wrote: a 3rd op whose target
    # directory is missing makes the vault raise after both writes.
    failing = TransactionOperation(
        operation="create",
        path="missing/x.md",
        before_exists=False,
        before_bytes=None,
        before_hash=None,
        after_bytes=b"# x\n",
        after_hash=sha256(b"# x\n"),
    )
    result = executor.execute("job-1", [*operations, failing])
    assert result.status == JobStatus.ROLLBACK_FAILED
    assert "f1.md" in result.conflict_paths
    # The external edit was NOT overwritten; f2 was restored; x never created.
    assert service.read_bytes("f1.md")[0] == b"external-wins\n"
    assert service.read_bytes("f2.md")[0] == B
    journal = history.journal("job-1")
    assert all(entry.state == "rolled_back" for entry in journal)


def test_clean_rollback_restores_every_file(
    vault_service_factory: object, tmp_path: Path
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    service = vault_service_factory(root)  # type: ignore[call-arg]
    service.create_bytes("f1.md", A)
    service.create_bytes("f2.md", B)
    operations = _ops(service, {"f1.md": b"a2\n", "f2.md": b"b2\n"})
    failing = TransactionOperation(
        operation="create",
        path="no/dir.md",
        before_exists=False,
        before_bytes=None,
        before_hash=None,
        after_bytes=b"x",
        after_hash=sha256(b"x"),
    )
    history = m7.make_history(tmp_path)
    history.record(job_id="job-clean", task_type="manual", status="executing")
    executor = TransactionExecutor(service, history=history)
    result = executor.execute("job-clean", [*operations, failing])
    assert result.status == JobStatus.ROLLED_BACK
    assert sorted(result.restored) == ["f1.md", "f2.md"]
    assert service.read_bytes("f1.md")[0] == A
    assert service.read_bytes("f2.md")[0] == B


def test_move_rollback_moves_the_file_back(
    vault_service_factory: object, tmp_path: Path
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    service = vault_service_factory(root)  # type: ignore[call-arg]
    service.create_bytes("src.md", A)
    move_op = TransactionOperation(
        operation="move",
        path="dst.md",  # destination path stored in journal
        before_exists=False,
        before_bytes=A,
        before_hash=sha256(A),
        after_bytes=A,
        after_hash=sha256(A),
        metadata={"source": "src.md"},
    )
    failing = TransactionOperation(
        operation="create",
        path="no_dir/md.md",
        before_exists=False,
        before_bytes=None,
        before_hash=None,
        after_bytes=b"x",
        after_hash=sha256(b"x"),
    )
    history = m7.make_history(tmp_path)
    history.record(job_id="job-move", task_type="manual", status="executing")
    executor = TransactionExecutor(service, history=history)
    result = executor.execute("job-move", [move_op, failing])
    assert result.status == JobStatus.ROLLED_BACK
    assert service.read_bytes("src.md")[0] == A
    with pytest.raises(Exception):
        service.read_bytes("dst.md")


def test_preflight_conflict_before_any_write_returns_conflict(
    vault_service_factory: object, tmp_path: Path
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    service = vault_service_factory(root)  # type: ignore[call-arg]
    service.create_bytes("f.md", A)
    history = m7.make_history(tmp_path)
    history.record(job_id="job-x", task_type="manual", status="executing")
    executor = TransactionExecutor(service, history=history)
    conflict = TransactionOperation(
        operation="update",
        path="f.md",
        before_exists=True,
        before_bytes=A,
        before_hash=sha256(b"stale-hash"),
        after_bytes=B,
        after_hash=sha256(B),
    )
    result = executor.execute("job-x", [conflict])
    assert result.status == JobStatus.CONFLICT
    assert service.read_bytes("f.md")[0] == A
