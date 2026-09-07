"""Startup crash diagnostics for M8 (PLAN-M8 §5.5, M8-07).

The scanner answers one question: *which derived records are uncertain after
a process interruption?*  It runs before the scheduler starts, marks nothing
by itself, and never writes note bytes, never rolls back and never calls a
model.  Marking happens only through :meth:`mark_recovery_required`, which
flips flagged rows to ``recovery_required`` and records the fixed reason
``process_interrupted`` so the user can diagnose or explicitly recover.
"""

from __future__ import annotations

from typing import Any

from server.history.service import HistoryService
from server.recovery.schemas import JobStatus
from server.scheduler.clock import Clock, SystemClock, iso_utc
from server.scheduler.models import SchedulerRunStatus

_FIXED_REASON = "process_interrupted"

# ai_jobs statuses that can only be present because the process died in the
# middle of an execution (normal runs go straight to awaiting_confirmation).
UNCERTAIN_JOB_STATUSES = (
    JobStatus.PLANNED.value,
    JobStatus.PREFLIGHTED.value,
    JobStatus.CAPTURED.value,
    JobStatus.EXECUTING.value,
    JobStatus.VALIDATING.value,
)
# scheduler runs that never finished because the process died.
UNCERTAIN_RUN_STATUSES = (
    SchedulerRunStatus.QUEUED.value,
    SchedulerRunStatus.RUNNING.value,
)


class RecoveryScanner:
    """Read-only scan + explicit derived-record marking.

    ``clock`` is injectable (S4) so tests can pin ``scanned_at``/mark times
    with the same fake clock the scheduler main path uses.
    """

    def __init__(
        self,
        history: HistoryService | None = None,
        *,
        clock: Clock | None = None,
    ) -> None:
        self.history = history
        self.clock = clock or SystemClock()

    def scan(self, history: HistoryService | None = None) -> dict[str, Any]:
        """Return an unmarked diagnosis of interrupted jobs/runs/journals."""
        service = history or self.history
        flagged_jobs: list[dict[str, object]] = []
        flagged_runs: list[dict[str, object]] = []
        scanned_at = iso_utc(self.clock.now_utc())
        if service is None or not service.available:
            return {
                "scanned_at": scanned_at,
                "flagged_jobs": flagged_jobs,
                "flagged_runs": flagged_runs,
                "total_recovery_required": 0,
                "scanned": False,
            }
        for row in service.unfinished_job_rows():
            job_id = str(row["job_id"])
            flagged_jobs.append(
                {
                    "job_id": job_id,
                    "status": str(row["status"]),
                    "reason": _FIXED_REASON,
                    "journal_uncertain": self._journal_uncertain(service, job_id),
                }
            )
        for row in service.runs_with_status(UNCERTAIN_RUN_STATUSES):
            flagged_runs.append(
                {
                    "run_id": str(row.get("run_id")),
                    "task": str(row.get("task")),
                    "status": str(row.get("status")),
                    "reason": _FIXED_REASON,
                }
            )
        total = len(flagged_jobs) + len(flagged_runs)
        return {
            "scanned_at": scanned_at,
            "flagged_jobs": flagged_jobs,
            "flagged_runs": flagged_runs,
            "total_recovery_required": total,
            "scanned": True,
        }

    @staticmethod
    def _journal_uncertain(history: HistoryService, job_id: str) -> bool:
        """pending/applied journal steps for a non-terminal job = uncertain."""
        try:
            entries = history.journal(job_id)
        except Exception:  # pragma: no cover - defensive
            return False
        return any(entry.state in ("pending", "applied") for entry in entries)

    def mark_recovery_required(
        self, history: HistoryService | None = None
    ) -> dict[str, Any]:
        """Flag uncertain records (derived writes only) after a crash scan.

        Marks scheduler runs ``running/queued`` and non-terminal ai_jobs as
        ``recovery_required`` with the fixed reason.  It performs no file
        writes, no rollback and no automatic accept — recovery stays an
        explicit user/API action.
        """
        service = history or self.history
        marked_jobs = 0
        marked_runs = 0
        if service is None or not service.available:
            return {"marked_jobs": 0, "marked_runs": 0, "reason": _FIXED_REASON}
        for row in service.unfinished_job_rows():
            job_id = str(row["job_id"])
            record = service.get(job_id)
            if record is None:
                continue
            updated = record.model_copy(
                update={
                    "status": JobStatus.RECOVERY_REQUIRED.value,
                    "error": {
                        "code": "process_interrupted",
                        "message": "process interrupted; explicit recovery required",
                    },
                }
            )
            service.save(updated)
            marked_jobs += 1
        now = iso_utc(self.clock.now_utc())
        for row in service.runs_with_status(UNCERTAIN_RUN_STATUSES):
            run = dict(row)
            run["status"] = SchedulerRunStatus.RECOVERY_REQUIRED.value
            run["error_code"] = _FIXED_REASON
            run["message"] = "process interrupted; explicit diagnosis required"
            run["finished_at"] = run.get("finished_at") or now
            service.save_run(run)
            marked_runs += 1
        return {"marked_jobs": marked_jobs, "marked_runs": marked_runs, "reason": _FIXED_REASON}


__all__ = ["RecoveryScanner"]
