"""Transaction executor with inverse rollback for M7 (PLAN-M7 §5.4).

Execution contract:

- every step is journaled (durably) *before* it writes;
- all writes go through :class:`server.vault.service.VaultService` with the
  captured expected hash, so external modifications surface as conflicts and
  are never overwritten;
- on any failure the completed steps are rolled back in reverse order with
  hash guards and post-restore verification; a rollback that cannot verify the
  restored bytes returns ``rollback_failed`` and refuses further writes.
"""

from __future__ import annotations

from typing import Any

from server.history.schemas import JournalEntry
from server.policies.rules import MAX_JOURNAL_BYTES
from server.vault.service import VaultService

from .journal import RecoveryJournal
from .schemas import JobStatus, RecoveryResult, TransactionOperation


class TransactionExecutor:
    """Runs one job's operation list inside a durable journal."""

    def __init__(
        self,
        vault: VaultService,
        history: Any = None,
        *,
        max_journal_bytes: int = MAX_JOURNAL_BYTES,
    ) -> None:
        self.vault = vault
        self.history = history
        self.max_journal_bytes = max_journal_bytes

    def execute(
        self,
        job_id: str,
        operations: list[TransactionOperation],
    ) -> RecoveryResult:
        """Execute all operations; on failure roll back the completed ones."""
        journal = RecoveryJournal(
            self.history, job_id=job_id, max_bytes=self.max_journal_bytes
        )
        executed: list[tuple[TransactionOperation, dict[str, Any], str]] = []
        try:
            for seq, operation in enumerate(operations):
                journal.record_operation(seq, operation)
                result = self._apply(operation)
                executed.append((operation, result, self._after_hash(operation, result)))
        except Exception as exc:
            if not executed:
                journal.mark("pending")
                return RecoveryResult(
                    job_id=job_id,
                    status=JobStatus.CONFLICT,
                    error=f"conflict before any write: {_safe_message(exc)}",
                )
            return self._rollback(job_id, executed, journal, exc)
        journal.mark("applied")
        return RecoveryResult(job_id=job_id, status=JobStatus.COMMITTED)

    # ------------------------------------------------------------------
    # Apply
    # ------------------------------------------------------------------

    def _apply(self, operation: TransactionOperation) -> dict[str, Any]:
        if operation.operation == "create":
            if operation.after_bytes is None:
                raise ValueError("create operation requires after_bytes")
            return self.vault.create_bytes(operation.path, operation.after_bytes)
        if operation.operation == "update":
            if operation.after_bytes is None:
                raise ValueError("update operation requires after_bytes")
            return self.vault.write_bytes(
                operation.path, operation.after_bytes, operation.before_hash
            )
        if operation.operation == "move":
            source = operation.metadata.get("source")
            if not source:
                raise ValueError("move operation requires a source path")
            return self.vault.move_file(source, operation.path, operation.before_hash)
        raise ValueError(f"unsupported transaction operation: {operation.operation}")

    # ------------------------------------------------------------------
    # Rollback (inverse, reverse order, hash-guarded, verified)
    # ------------------------------------------------------------------

    def _rollback(
        self,
        job_id: str,
        executed: list[tuple[TransactionOperation, dict[str, Any]]],
        journal: RecoveryJournal,
        cause: Exception,
    ) -> RecoveryResult:
        restored: list[str] = []
        for operation, _result, after_hash in reversed(executed):
            try:
                self._undo_one(operation, after_hash)
            except Exception as rollback_error:
                journal.mark("rolled_back")
                return RecoveryResult(
                    job_id=job_id,
                    status=JobStatus.ROLLBACK_FAILED,
                    restored=restored,
                    conflict_paths=[operation.path],
                    error=(
                        f"rollback failed on {operation.path}: "
                        f"{_safe_message(rollback_error)}"
                    ),
                )
            restored.append(operation.path)
            if operation.operation == "move":
                source = operation.metadata.get("source")
                if source and source not in restored:
                    restored.append(source)
        journal.mark("rolled_back")
        return RecoveryResult(
            job_id=job_id,
            status=JobStatus.ROLLED_BACK,
            restored=restored,
            error=f"transaction failed: {_safe_message(cause)}",
        )

    @staticmethod
    def _after_hash(operation: TransactionOperation, result: dict[str, Any]) -> str:
        """The authoritative post-write hash from the vault result."""
        digest = result.get("sha256")
        if digest:
            return str(digest)
        from server.actions.diff import sha256 as digest_bytes

        if operation.after_bytes is not None:
            return digest_bytes(operation.after_bytes)
        raise RuntimeError("cannot derive the after hash for rollback")

    def _undo_one(self, operation: TransactionOperation, after_hash: str) -> None:
        """Restore one operation's before-state; hash guard on every step."""
        if operation.operation == "create":
            self.vault.delete_file(operation.path, expected_sha256=after_hash)
            self._assert_absent(operation.path)
            return
        if operation.operation == "update":
            if operation.before_bytes is None:
                raise ValueError("update inverse requires before bytes")
            result = self.vault.write_bytes(
                operation.path,
                operation.before_bytes,
                expected_sha256=after_hash,
            )
            if result.get("sha256") != operation.before_hash:
                raise RuntimeError("restored bytes do not match the before hash")
            return
        if operation.operation == "move":
            source = operation.metadata.get("source")
            if not source:
                raise ValueError("move inverse requires a source path")
            result = self.vault.move_file(
                operation.path, source, expected_sha256=after_hash
            )
            if operation.before_hash and result.get("sha256") != operation.before_hash:
                raise RuntimeError("moved-back bytes do not match the before hash")
            return
        raise ValueError(f"unsupported inverse for {operation.operation}")

    def _assert_absent(self, path: str) -> None:
        try:
            self.vault.read_bytes(path)
        except Exception as exc:
            code = getattr(exc, "code", None)
            if code is not None and code.value == "not_found":
                return
            raise
        raise RuntimeError("file still exists after inverse delete")


def _safe_message(exc: Exception) -> str:
    """Return a fixed, relative-only error line (never a traceback)."""
    text = str(exc).strip() or exc.__class__.__name__
    return text[:500]


__all__ = ["JournalEntry", "RecoveryJournal", "TransactionExecutor"]
