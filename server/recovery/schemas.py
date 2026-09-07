"""Transaction and recovery DTOs for M7 (PLAN-M7 §5.4)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class JobStatus(StrEnum):
    PLANNED = "planned"
    PREFLIGHTED = "preflighted"
    CAPTURED = "captured"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    EXECUTING = "executing"
    VALIDATING = "validating"
    COMMITTED = "committed"
    REJECTED = "rejected"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"
    CONFLICT = "conflict"
    UNDONE = "undone"
    UNDO_UNAVAILABLE = "undo_unavailable"
    ROLLBACK_FAILED = "rollback_failed"
    # M8 additive marker: an interrupted execution needs explicit diagnosis
    # before anything else may touch it (PLAN-M8 §5.5).  Never auto-written.
    RECOVERY_REQUIRED = "recovery_required"


@dataclass(frozen=True)
class TransactionOperation:
    """One deterministic, journaled step of a job transaction.

    ``operation`` is one of ``create``/``update``/``move``; the executor may
    additionally perform an internal, hash-guarded ``delete`` only as the
    inverse of a ``create`` (never as a model action).
    """

    operation: Literal["create", "update", "move"]
    path: str
    before_exists: bool
    before_bytes: bytes | None
    before_hash: str | None
    after_bytes: bytes | None
    after_hash: str | None
    target_path: str | None = None
    #: Extra destination/source context for move operations.
    metadata: dict[str, Any] = field(default_factory=dict)

    def inverse_kind(self) -> str:
        if self.operation == "create":
            return "delete_after"
        if self.operation == "move":
            return "move_back"
        return "write_before"


class TransactionState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    status: JobStatus = JobStatus.PLANNED
    error: str | None = None
    completed: int = 0
    total: int = 0


class BeforeState(BaseModel):
    """Captured pre-mutation state of one file (bytes bounded by journal)."""

    model_config = ConfigDict(extra="forbid")

    path: str
    exists: bool
    content_base64: str | None = None
    sha256: str | None = None
    byte_length: int = Field(default=0, ge=0)


class JournalRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    seq: int = Field(ge=0)
    operation: str
    path: str
    before: BeforeState
    after_exists: bool = False
    after_hash: str | None = None
    inverse: dict[str, Any] = Field(default_factory=dict)
    state: str = "pending"


class RecoveryResult(BaseModel):
    """Outcome of one transaction execution, rollback or undo pass."""

    model_config = ConfigDict(extra="forbid")

    job_id: str
    status: JobStatus
    restored: list[str] = Field(default_factory=list)
    conflict_paths: list[str] = Field(default_factory=list)
    error: str | None = None


class JournalDiagnosis(BaseModel):
    """One journal step's read-only diagnostic view (M8 explicit recovery)."""

    model_config = ConfigDict(extra="forbid")

    seq: int = Field(ge=0)
    operation: str
    path: str
    state: str
    before_hash: str | None = None
    after_hash: str | None = None
    current_matches_after: bool = False


class JobDiagnosis(BaseModel):
    """Read-only crash diagnosis of one uncertain ai_jobs record (M8 §5.5)."""

    model_config = ConfigDict(extra="forbid")

    job_id: str
    status: str
    task_type: str
    flagged_reason: str = "process_interrupted"
    journal_steps: list[JournalDiagnosis] = Field(default_factory=list)
    journal_uncertain: bool = False


class RecoveryScanResult(BaseModel):
    """Startup crash-scan outcome (marks nothing; purely diagnostic)."""

    model_config = ConfigDict(extra="forbid")

    scanned_at: str
    flagged_jobs: list[dict[str, object]] = Field(default_factory=list)
    flagged_runs: list[dict[str, object]] = Field(default_factory=list)
    total_recovery_required: int = 0


UndoResult = RecoveryResult

__all__ = [
    "BeforeState",
    "JobDiagnosis",
    "JobStatus",
    "JournalDiagnosis",
    "JournalRecord",
    "RecoveryResult",
    "RecoveryScanResult",
    "TransactionOperation",
    "TransactionState",
    "UndoResult",
]
