"""Durable transaction journal facade for M7 (PLAN-M7 §5.4, M7-07).

The journal persists one row per planned step *before* the step writes, so a
crash can always distinguish "not started" from "wrote but not recorded".
Before bytes are stored bounded and base64-encoded; exceeding the journal cap
is a preflight error, never a silent partial backup.
"""

from __future__ import annotations

import base64
from typing import Any

from server.actions.diff import sha256
from server.history.schemas import JournalEntry
from server.policies.errors import HistoryLimitExceeded
from server.policies.rules import MAX_JOURNAL_BYTES

from .schemas import TransactionOperation


def encode(data: bytes | None) -> str | None:
    return base64.b64encode(data).decode("ascii") if data is not None else None


def decode(value: str | None) -> bytes:
    return base64.b64decode(value) if value else b""


def make_entry(job_id: str, seq: int, operation: TransactionOperation) -> JournalEntry:
    """Build the journal row for one transaction step."""
    inverse: dict[str, Any] = {"kind": operation.inverse_kind()}
    if operation.operation == "move":
        inverse["source"] = operation.metadata.get("source", "")
    return JournalEntry(
        job_id=job_id,
        seq=seq,
        operation=operation.operation,
        path=operation.target_path or operation.path,
        before_exists=operation.before_exists,
        before_bytes_base64=encode(operation.before_bytes),
        before_hash=operation.before_hash,
        after_exists=True,
        after_hash=operation.after_hash or (
            sha256(operation.after_bytes) if operation.after_bytes is not None else None
        ),
        inverse=inverse,
        state="pending",
    )


class RecoveryJournal:
    """Journal sink bound to one job; durable when a HistoryService is set."""

    def __init__(
        self,
        history: Any = None,
        *,
        job_id: str | None = None,
        max_bytes: int = MAX_JOURNAL_BYTES,
    ) -> None:
        self.history = history
        self.job_id = job_id
        self.max_bytes = max_bytes
        self._pending: list[JournalEntry] = []
        self._bytes = 0

    def record(self, entry: JournalEntry) -> JournalEntry:
        if self.job_id and entry.job_id != self.job_id:
            raise ValueError("journal job mismatch")
        self._bytes += len(entry.before_bytes_base64 or "")
        if self._bytes > self.max_bytes:
            raise HistoryLimitExceeded("Journal before-state exceeds the size limit")
        if self.history is not None:
            self.history.append_journal(entry)
        self._pending.append(entry)
        return entry

    def record_operation(self, seq: int, operation: TransactionOperation) -> JournalEntry:
        return self.record(make_entry(self.job_id or "", seq, operation))

    def mark(self, state: str) -> None:
        if self.history is not None and self.job_id:
            self.history.set_journal_state(self.job_id, state)

    def entries(self) -> list[JournalEntry]:
        if self.history is not None and self.job_id:
            return self.history.journal(self.job_id)
        return list(self._pending)


__all__ = ["RecoveryJournal", "decode", "encode", "make_entry"]
