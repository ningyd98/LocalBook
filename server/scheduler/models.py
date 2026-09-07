"""Scheduler DTOs for M8 (PLAN-M8 §5.2/§5.4).

Only schedule/dispatch metadata lives here — no business logic, no prompt
text, no file writes.  Run statuses are intentionally separate from the M7
:class:`server.recovery.schemas.JobStatus` vocabulary (PLAN-M8 §6.3): the
scheduler records *how a trigger went* (previewed/committed/failed/…), the
M7 History records the *job state* (awaiting_confirmation/committed/…).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

TASK_IDS = Literal["daily_organizer", "weekly_review", "index_consistency"]
TRIGGER_KINDS = Literal["scheduled", "manual", "startup"]


class SchedulerRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PREVIEWED = "previewed"
    COMMITTED = "committed"
    SKIPPED_DUPLICATE = "skipped_duplicate"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    RECOVERY_REQUIRED = "recovery_required"

    @property
    def finished(self) -> bool:
        """A run whose outcome is final (nothing more will transition it)."""
        return self in {
            SchedulerRunStatus.PREVIEWED,
            SchedulerRunStatus.COMMITTED,
            SchedulerRunStatus.SKIPPED_DUPLICATE,
            SchedulerRunStatus.FAILED,
            SchedulerRunStatus.TIMED_OUT,
        }

    @property
    def active(self) -> bool:
        return self in {
            SchedulerRunStatus.QUEUED,
            SchedulerRunStatus.RUNNING,
        }

    @property
    def recovery(self) -> bool:
        return self == SchedulerRunStatus.RECOVERY_REQUIRED


class JobDefinition(BaseModel):
    """One statically registered task definition (stable id + schedule)."""

    model_config = ConfigDict(extra="forbid")

    job_id: TASK_IDS
    trigger: Literal["cron", "interval"]
    expression: str
    timezone: str = "UTC"
    enabled: bool = True


class SchedulerRun(BaseModel):
    """One auditable scheduler run record (PLAN-M8 §5.4 DTO).

    ``idempotency_key`` is a deterministic audit label of the form
    ``task:scheduled_for``; it is not a database-unique key.  Duplicate
    protection for one slot is done by the service's active-run gate and the
    per-task lock, never by this column, so repeated triggers in the same slot
    intentionally append fresh run rows.
    """

    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    task: TASK_IDS
    trigger: TRIGGER_KINDS
    idempotency_key: str = Field(min_length=1, max_length=256)
    status: SchedulerRunStatus
    agent_job_id: str | None = Field(default=None, max_length=128)
    scheduled_for: str | None = None
    started_at: str
    finished_at: str | None = None
    error_code: str | None = Field(default=None, max_length=64)
    message: str | None = Field(default=None, max_length=2000)


class RunHandle(BaseModel):
    """Synchronous handle for a backend ``run_now`` trigger."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    status: SchedulerRunStatus = SchedulerRunStatus.QUEUED


def to_row(run: SchedulerRun) -> dict[str, object]:
    """Project a validated run into the ``scheduler_runs`` row shape."""
    return {
        "run_id": str(run.run_id),
        "task": run.task,
        "trigger": run.trigger,
        "status": run.status.value,
        "idempotency_key": run.idempotency_key,
        "agent_job_id": run.agent_job_id,
        "scheduled_for": run.scheduled_for,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "error_code": run.error_code,
        "message": run.message,
    }


__all__ = [
    "JobDefinition",
    "RunHandle",
    "SchedulerRun",
    "SchedulerRunStatus",
    "TASK_IDS",
    "TRIGGER_KINDS",
    "to_row",
]
