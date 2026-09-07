"""Recovery orchestration boundary (M7, PLAN-M7 M7-07/M7-08; M8 §5.5)."""

from __future__ import annotations

from typing import Any

from server.history.schemas import JournalEntry
from server.history.service import HistoryService
from server.vault.service import VaultService

from .executor import TransactionExecutor
from .journal import decode
from .schemas import (
    JobDiagnosis,
    JobStatus,
    JournalDiagnosis,
    RecoveryResult,
    TransactionOperation,
)
from .undo import UndoService

_EXECUTABLE_OPERATIONS = {"create", "update", "move"}


class RecoveryService:
    """Executes journaled transactions, model-free undo and explicit recovery."""

    def __init__(
        self,
        vault: VaultService,
        history: HistoryService | Any = None,
        *,
        max_journal_bytes: int = 10_000_000,
    ) -> None:
        self.vault = vault
        self.history = history
        self.executor = TransactionExecutor(
            vault, history=history, max_journal_bytes=max_journal_bytes
        )
        self.undo_service = UndoService(vault, history=history)

    def execute(
        self,
        job_id: str,
        operations: list[TransactionOperation],
    ) -> RecoveryResult:
        return self.executor.execute(job_id, operations)

    def undo(self, job_id: str) -> RecoveryResult:
        return self.undo_service.undo(job_id)

    # ------------------------------------------------------------------
    # M8: read-only diagnosis + explicit, hash-guarded rollback
    # (never guessed at startup; user/API initiated only)
    # ------------------------------------------------------------------

    def _current_hash(self, path: str) -> str | None:
        """Current vault hash for a path (``None`` when the file is absent)."""
        try:
            _data, digest = self.vault.read_bytes(path)
            return digest
        except Exception as exc:
            code = getattr(exc, "code", None)
            if code is not None and code.value == "not_found":
                return None
            return None  # unreadable -> treated as not verifiable (None)

    def diagnose(self, job_id: str) -> JobDiagnosis:
        """Read-only diagnosis of one uncertain job (never writes)."""
        record = self.history.get(job_id) if self.history is not None else None
        status = str(record.status) if record is not None else "missing"
        steps: list[JournalDiagnosis] = []
        uncertain = False
        if self.history is not None:
            for entry in self.history.journal(job_id):
                current = self._current_hash(entry.path)
                steps.append(
                    JournalDiagnosis(
                        seq=entry.seq,
                        operation=entry.operation,
                        path=entry.path,
                        state=entry.state,
                        before_hash=entry.before_hash,
                        after_hash=entry.after_hash,
                        current_matches_after=(
                            entry.after_hash is not None and current == entry.after_hash
                        ),
                    )
                )
            uncertain = any(
                step.state in ("pending", "applied")
                and step.operation in _EXECUTABLE_OPERATIONS
                for step in steps
            )
        return JobDiagnosis(
            job_id=job_id,
            status=status,
            task_type=str(record.task_type) if record is not None else "",
            journal_uncertain=uncertain,
            journal_steps=steps,
        )

    def rollback_if_safe(self, job_id: str) -> RecoveryResult:
        """Explicit rollback of an uncertain job, guarded by after-hashes.

        Only runs when the record is still flagged (``recovery_required`` or
        an unfinished execution state) and every journal step's current vault
        hash either matches the recorded after-hash (written by us → restore
        inverse) or is untouched (never applied → skip).  Any external change
        aborts before a single write (``conflict``); a failure mid-restore
        leaves the record flagged and reports ``rollback_failed``.
        """
        if self.history is None:
            return RecoveryResult(
                job_id=job_id,
                status=JobStatus.UNDO_UNAVAILABLE,
                error="history unavailable for explicit recovery",
            )
        record = self.history.get(job_id)
        if record is None:
            return RecoveryResult(
                job_id=job_id,
                status=JobStatus.UNDO_UNAVAILABLE,
                error="job not found; nothing to recover",
            )
        flagged = record.status in (
            JobStatus.RECOVERY_REQUIRED.value,
            JobStatus.EXECUTING.value,
            JobStatus.VALIDATING.value,
            JobStatus.CAPTURED.value,
            JobStatus.PREFLIGHTED.value,
            JobStatus.PLANNED.value,
        )
        if not flagged:
            return RecoveryResult(
                job_id=job_id,
                status=JobStatus.UNDO_UNAVAILABLE,
                error="job is not in an uncertain state; use /history undo for committed jobs",
            )
        entries = self.history.journal(job_id)
        executable = [
            entry
            for entry in entries
            if entry.operation in _EXECUTABLE_OPERATIONS
            and entry.state in ("pending", "applied")
        ]
        if not executable:
            # Nothing was ever journaled → nothing was ever written; the job
            # had no durable effect, so it can be closed as failed.
            self._finish_recovery_record(
                job_id,
                status=JobStatus.FAILED,
                code="recovery_nothing_applied",
                message="no journaled steps were applied",
            )
            return RecoveryResult(
                job_id=job_id,
                status=JobStatus.ROLLED_BACK,
                restored=[],
                error="nothing to restore",
            )

        # Pass 1 — classify every step against current vault hashes; any
        # external change aborts before the first write.
        applied: list[tuple[JournalEntry, str]] = []
        conflict_paths: list[str] = []
        for entry in executable:
            current = self._current_hash(entry.path)
            if entry.after_hash is not None and current == entry.after_hash:
                applied.append((entry, entry.after_hash))
            elif current is None or current == entry.before_hash:
                continue  # never applied (or deleted back) — nothing to do
            else:
                conflict_paths.append(entry.path)
        if conflict_paths:
            return RecoveryResult(
                job_id=job_id,
                status=JobStatus.CONFLICT,
                conflict_paths=conflict_paths,
                error="files changed externally; refusing to overwrite",
            )

        # Pass 2 — restore in reverse order with per-step verification.
        restored: list[str] = []
        next_seq = max((entry.seq for entry in entries), default=-1) + 1
        for entry, after_hash in reversed(applied):
            try:
                self._restore_entry(entry, after_hash)
            except Exception as exc:
                self.history.set_journal_state(job_id, "rolled_back")
                return RecoveryResult(
                    job_id=job_id,
                    status=JobStatus.ROLLBACK_FAILED,
                    restored=restored,
                    conflict_paths=[entry.path],
                    error=f"explicit rollback failed on {entry.path}: {_safe(exc)}",
                )
            restored.append(entry.path)
            if entry.operation == "move":
                source = entry.inverse.get("source") if entry.inverse else None
                if source and source not in restored:
                    restored.append(source)
            self._append_audit(job_id, entry, next_seq)
            next_seq += 1
        self.history.set_journal_state(job_id, "rolled_back")
        self._finish_recovery_record(
            job_id,
            status=JobStatus.ROLLED_BACK,
            code="recovery_rolled_back",
            message="explicit recovery rollback complete",
        )
        return RecoveryResult(
            job_id=job_id,
            status=JobStatus.ROLLED_BACK,
            restored=restored,
            error="explicit recovery rollback complete",
        )

    def _finish_recovery_record(
        self,
        job_id: str,
        *,
        status: JobStatus,
        code: str,
        message: str,
    ) -> None:
        record = self.history.get(job_id) if self.history is not None else None
        if record is None:
            return
        updated = record.model_copy(
            update={
                "status": status.value,
                "error": {"code": code, "message": message},
                "end_time": record.end_time,
            }
        )
        self.history.save(updated)

    def _restore_entry(self, entry: JournalEntry, after_hash: str) -> None:
        """One hash-guarded inverse (same semantics as transaction rollback)."""
        if entry.operation == "update":
            before = decode(entry.before_bytes_base64)
            result = self.vault.write_bytes(
                entry.path, before, expected_sha256=after_hash
            )
            if entry.before_hash and result.get("sha256") != entry.before_hash:
                raise RuntimeError("restored bytes do not match the before hash")
            return
        if entry.operation == "create":
            self.vault.delete_file(entry.path, expected_sha256=after_hash)
            self._assert_absent(entry.path)
            return
        if entry.operation == "move":
            source = entry.inverse.get("source") if entry.inverse else None
            if not source:
                raise RuntimeError("move inverse requires a source path")
            result = self.vault.move_file(
                entry.path, source, expected_sha256=after_hash
            )
            if entry.before_hash and result.get("sha256") != entry.before_hash:
                raise RuntimeError("moved-back bytes do not match the before hash")
            return
        raise RuntimeError(f"unsupported inverse for {entry.operation}")

    def _assert_absent(self, path: str) -> None:
        try:
            self.vault.read_bytes(path)
        except Exception as exc:
            code = getattr(exc, "code", None)
            if code is not None and code.value == "not_found":
                return
            raise
        raise RuntimeError("file still exists after inverse delete")

    def _append_audit(self, job_id: str, entry: JournalEntry, seq: int) -> None:
        if self.history is None:
            return
        self.history.append_journal(
            JournalEntry(
                job_id=job_id,
                seq=seq,
                operation="undo",
                path=entry.path,
                before_exists=False,
                before_bytes_base64=None,
                before_hash=entry.before_hash,
                after_exists=False,
                after_hash=None,
                inverse={},
                state="applied",
            )
        )


def _safe(exc: Exception) -> str:
    return str(exc).strip()[:500] or exc.__class__.__name__


__all__ = ["JournalEntry", "RecoveryResult", "RecoveryService", "TransactionOperation"]
