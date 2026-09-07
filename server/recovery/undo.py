"""Hash-guarded Undo for M7 (PLAN-M7 §5.4, M7-08).

Undo never calls a model and never re-runs the planner: it restores the
journaled before-states in reverse execution order, refusing to overwrite any
file whose current hash no longer matches the recorded after-hash.  A new
``undo`` journal audit row is appended per restored path and the original job
record keeps its content intact.
"""

from __future__ import annotations

from typing import Any

from server.history.schemas import JournalEntry
from server.vault.service import VaultService

from .journal import decode
from .schemas import JobStatus, RecoveryResult

_EXECUTABLE_OPERATIONS = {"create", "update", "move"}


class UndoService:
    """Restores committed jobs from their durable journal (no LLM involved)."""

    def __init__(self, vault: VaultService, history: Any = None) -> None:
        self.vault = vault
        self.history = history

    def undo(self, job_id: str) -> RecoveryResult:
        entries = self.history.journal(job_id) if self.history is not None else []
        if not entries:
            return RecoveryResult(
                job_id=job_id,
                status=JobStatus.UNDO_UNAVAILABLE,
                error="no journal for this job",
            )
        executed = [entry for entry in entries if entry.operation in _EXECUTABLE_OPERATIONS]
        if any(entry.operation == "undo" for entry in entries):
            return RecoveryResult(
                job_id=job_id,
                status=JobStatus.UNDO_UNAVAILABLE,
                error="job has already been undone",
            )
        restored: list[str] = []
        next_seq = max((entry.seq for entry in entries), default=-1) + 1
        try:
            for entry in reversed(executed):
                self._restore_one(entry, job_id, next_seq)
                restored.append(entry.path)
                next_seq += 1
        except _Conflict as conflict:
            return RecoveryResult(
                job_id=job_id,
                status=JobStatus.CONFLICT,
                restored=restored,
                conflict_paths=[conflict.path],
                error="file changed externally",
            )
        return RecoveryResult(job_id=job_id, status=JobStatus.UNDONE, restored=restored)

    # ------------------------------------------------------------------
    # Per-entry restore
    # ------------------------------------------------------------------

    def _restore_one(self, entry: JournalEntry, job_id: str, seq: int) -> None:
        if entry.operation == "move":
            source = entry.inverse.get("source") if entry.inverse else None
            if not source:
                raise _Conflict(entry.path)
            self._require_current(entry.path, entry.after_hash)
            result = self.vault.move_file(
                entry.path, source, expected_sha256=entry.after_hash
            )
            if entry.before_hash and result.get("sha256") != entry.before_hash:
                raise _Conflict(entry.path)
            self._append_audit(job_id, seq, source, entry.before_hash)
            return
        if entry.operation == "create":
            self._require_current(entry.path, entry.after_hash)
            self.vault.delete_file(entry.path, expected_sha256=entry.after_hash)
            self._assert_absent(entry.path)
            self._append_audit(job_id, seq, entry.path, None)
            return
        # update
        self._require_current(entry.path, entry.after_hash)
        before = decode(entry.before_bytes_base64)
        result = self.vault.write_bytes(
            entry.path, before, expected_sha256=entry.after_hash
        )
        if result.get("sha256") != entry.before_hash:
            raise _Conflict(entry.path)
        self._append_audit(job_id, seq, entry.path, entry.before_hash)

    def _require_current(self, path: str, expected_hash: str | None) -> None:
        try:
            _data, current = self.vault.read_bytes(path)
        except Exception:
            raise _Conflict(path) from None
        if current != expected_hash:
            raise _Conflict(path)

    def _assert_absent(self, path: str) -> None:
        try:
            self.vault.read_bytes(path)
        except Exception as exc:
            code = getattr(exc, "code", None)
            if code is not None and code.value == "not_found":
                return
            raise
        raise _Conflict(path)

    def _append_audit(
        self,
        job_id: str,
        seq: int,
        path: str,
        restored_hash: str | None,
    ) -> None:
        if self.history is None:
            return
        self.history.append_journal(
            JournalEntry(
                job_id=job_id,
                seq=seq,
                operation="undo",
                path=path,
                before_exists=False,
                before_bytes_base64=None,
                before_hash=restored_hash,
                after_exists=False,
                after_hash=None,
                inverse={},
                state="applied",
            )
        )


class _Conflict(Exception):
    def __init__(self, path: str) -> None:
        super().__init__(path)
        self.path = path


__all__ = ["UndoService"]
