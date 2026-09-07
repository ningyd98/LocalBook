"""History lifecycle service (M7, PLAN-M7 §5.5).

Single entry point for persisting job records and journal steps, paginating
History, and constructing API DTOs.  The service never writes note bytes — it
only persists derived audit state.  With no repository attached it keeps an
in-memory mirror (unit tests / repository-less runs); production wiring always
passes a repository-backed instance.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .repository import HistoryRepository
from .schemas import HistoryPage, HistoryRecord, JournalEntry

_DEFAULT_PAGE_SIZE = 20
_MAX_PAGE_SIZE = 100


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class HistoryService:
    """History facade; repository-less instances use an in-memory mirror."""

    def __init__(self, repository: HistoryRepository | None = None) -> None:
        self.repository = repository
        self._records: dict[str, HistoryRecord] = {}
        self._journal: dict[str, list[JournalEntry]] = {}
        self._runs: dict[str, dict[str, object]] = {}

    @property
    def available(self) -> bool:
        return self.repository is not None

    # ------------------------------------------------------------------
    # Records
    # ------------------------------------------------------------------

    def record(self, **kwargs: Any) -> HistoryRecord:
        return self.save(HistoryRecord(**kwargs))

    def save(self, record: HistoryRecord) -> HistoryRecord:
        if self.repository is not None:
            self.repository.save(record)
        self._records[record.job_id] = record
        return record

    def get(self, job_id: str) -> HistoryRecord | None:
        if self.repository is not None:
            return self.repository.get(job_id)
        return self._records.get(job_id)

    def page(
        self,
        *,
        limit: int = _DEFAULT_PAGE_SIZE,
        offset: int = 0,
        status: str | None = None,
    ) -> HistoryPage:
        safe_limit = max(1, min(int(limit), _MAX_PAGE_SIZE))
        safe_offset = max(0, int(offset))
        if self.repository is not None:
            items, total = self.repository.page(
                limit=safe_limit, offset=safe_offset, status=status
            )
            return HistoryPage(items=items, total=total, limit=safe_limit, offset=safe_offset)
        items = list(self._records.values())
        if status:
            items = [record for record in items if record.status == status]
        items.sort(key=lambda record: (record.start_time, record.job_id), reverse=True)
        return HistoryPage(
            items=items[safe_offset : safe_offset + safe_limit],
            total=len(items),
            limit=safe_limit,
            offset=safe_offset,
        )

    def finish(
        self,
        record: HistoryRecord,
        *,
        status: str,
        error: dict[str, Any] | None = None,
    ) -> HistoryRecord:
        """Return (and persist) a record copy with its terminal state set."""
        finished = record.model_copy(
            update={
                "status": status,
                "error": error,
                "end_time": _now_iso(),
            }
        )
        return self.save(finished)

    # ------------------------------------------------------------------
    # Journal
    # ------------------------------------------------------------------

    def append_journal(self, entry: JournalEntry) -> None:
        if self.repository is None:
            bucket = self._journal.setdefault(entry.job_id, [])
            bucket.append(entry)
            bucket.sort(key=lambda item: item.seq)
            return
        self.repository.add_journal(entry)

    def journal(self, job_id: str) -> list[JournalEntry]:
        if self.repository is not None:
            return self.repository.journal(job_id)
        return list(self._journal.get(job_id, []))

    def set_journal_state(self, job_id: str, state: str) -> None:
        if self.repository is not None:
            self.repository.set_journal_state(job_id, state)
            return
        for entry in self._journal.get(job_id, []):
            entry.state = state

    # ------------------------------------------------------------------
    # Scheduler run records (M8; same derived persistence rules)
    # ------------------------------------------------------------------

    def save_run(self, row: dict[str, object]) -> dict[str, object]:
        if self.repository is not None:
            self.repository.save_run(row)
        self._runs[str(row["run_id"])] = row
        return row

    def get_run(self, run_id: str) -> dict[str, object] | None:
        if self.repository is not None:
            return self.repository.get_run(run_id)
        return self._runs.get(run_id)

    def page_runs(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        task: str | None = None,
    ) -> dict[str, object]:
        safe_limit = max(1, min(int(limit), 100))
        safe_offset = max(0, int(offset))
        if self.repository is not None:
            items, total = self.repository.page_runs(
                limit=safe_limit, offset=safe_offset, task=task
            )
            return {"items": items, "total": total, "limit": safe_limit, "offset": safe_offset}
        items = list(self._runs.values())
        if task:
            items = [item for item in items if item.get("task") == task]
        items.sort(
            key=lambda item: (str(item.get("started_at", "")), str(item.get("run_id", ""))),
            reverse=True,
        )
        return {
            "items": items[safe_offset : safe_offset + safe_limit],
            "total": len(items),
            "limit": safe_limit,
            "offset": safe_offset,
        }

    def runs_with_status(self, statuses: tuple[str, ...]) -> list[dict[str, object]]:
        if self.repository is not None:
            return self.repository.runs_with_status(statuses)
        wanted = set(statuses)
        items = [item for item in self._runs.values() if item.get("status") in wanted]
        items.sort(key=lambda item: str(item.get("started_at", "")))
        return items

    # ------------------------------------------------------------------
    # M8 recovery diagnostics / retention
    # ------------------------------------------------------------------

    def unfinished_job_rows(self) -> list[dict[str, object]]:
        """Rows of ai_jobs in non-terminal execution states (crash scan)."""
        from server.recovery.schemas import JobStatus

        states = (
            JobStatus.PLANNED.value,
            JobStatus.PREFLIGHTED.value,
            JobStatus.CAPTURED.value,
            JobStatus.EXECUTING.value,
            JobStatus.VALIDATING.value,
        )
        if self.repository is not None:
            return self.repository.job_ids_with_statuses(states)
        wanted = set(states)
        return [
            {
                "job_id": key,
                "status": record.status,
                "end_time": record.end_time,
            }
            for key, record in self._records.items()
            if record.status in wanted
        ]

    def recovery_required_count(self) -> int:
        """Distinct recovery records: flagged runs + flagged jobs w/o flagged run."""
        from server.recovery.schemas import JobStatus
        from server.scheduler.models import SchedulerRunStatus

        flagged_job = JobStatus.RECOVERY_REQUIRED.value
        flagged_run = SchedulerRunStatus.RECOVERY_REQUIRED.value
        runs = self.runs_with_status((flagged_run,))
        flagged_run_job_ids = {
            str(item.get("agent_job_id")) for item in runs if item.get("agent_job_id")
        }
        if self.repository is not None:
            jobs = self.repository.job_ids_with_statuses((flagged_job,))
        else:
            jobs = [
                {"job_id": key, "status": record.status, "end_time": record.end_time}
                for key, record in self._records.items()
                if record.status == flagged_job
            ]
        orphan_jobs = [
            row for row in jobs if str(row["job_id"]) not in flagged_run_job_ids
        ]
        return len(runs) + len(orphan_jobs)

    # ------------------------------------------------------------------
    # Retention cleanup (M8 §5.6) — derived rows only, Vault never touched
    # ------------------------------------------------------------------

    def cleanup_retention(
        self,
        *,
        now_iso: str | None = None,
        retention_days: int = 30,
        max_scheduler_runs: int = 1000,
    ) -> dict[str, int]:
        """Delete expired terminal ai_jobs/journals and scheduler runs.

        Rules (PLAN-M8 §5.6): only terminal rows whose finished bound is older
        than ``now - retention_days``; ``awaiting_confirmation``, active and
        ``recovery_required`` records are always kept; the newest run per task
        is kept; journal rows cascade with their job.  Markdown bytes/hash are
        never read or written here.  Deleting a job removes its Undo material
        — callers surface that as "deleted history is not undoable".
        """
        cutoff = now_iso or _now_iso()
        try:
            from datetime import UTC as _UTC
            from datetime import timedelta

            from server.scheduler.clock import parse_iso

            cutoff_dt = parse_iso(cutoff) - timedelta(days=int(retention_days))
            bound = cutoff_dt.astimezone(_UTC).isoformat()
        except Exception:
            bound = cutoff

        from server.recovery.schemas import JobStatus

        job_terminal = (
            JobStatus.COMMITTED.value,
            JobStatus.REJECTED.value,
            JobStatus.ROLLED_BACK.value,
            JobStatus.FAILED.value,
            JobStatus.CONFLICT.value,
            JobStatus.UNDONE.value,
            JobStatus.UNDO_UNAVAILABLE.value,
            JobStatus.ROLLBACK_FAILED.value,
        )
        from server.scheduler.models import SchedulerRunStatus

        run_terminal = (
            SchedulerRunStatus.PREVIEWED.value,
            SchedulerRunStatus.COMMITTED.value,
            SchedulerRunStatus.SKIPPED_DUPLICATE.value,
            SchedulerRunStatus.FAILED.value,
            SchedulerRunStatus.TIMED_OUT.value,
        )

        deleted_jobs = 0
        deleted_runs = 0
        kept_active_runs = 0
        if self.repository is not None:
            job_rows = self.repository.terminal_job_rows(job_terminal, bound)
            deleted_jobs = self.repository.delete_jobs(
                [str(row["job_id"]) for row in job_rows]
            )
            run_rows = self.repository.terminal_run_rows(run_terminal, bound)
            run_ids = [str(row["run_id"]) for row in run_rows]
            # Keep the newest run per task no matter how old it is.
            newest_per_task: dict[str, str | None] = {}
            all_runs = self.repository.all_runs_sorted()
            for row in all_runs:
                task = str(row.get("task", ""))
                newest_per_task.setdefault(task, str(row.get("run_id", "")))
            keep_ids = set(newest_per_task.values())
            deletable = [run_id for run_id in run_ids if run_id not in keep_ids]
            kept_active_runs = len(run_ids) - len(deletable)
            deleted_runs = self.repository.delete_runs(deletable)

            total_runs = self.repository.run_count()
            excess = max(0, total_runs - int(max_scheduler_runs))
            if excess:
                oldest = self.repository.oldest_terminal_runs(run_terminal, excess)
                ids = [str(row["run_id"]) for row in oldest]
                if ids:
                    deleted_runs += self.repository.delete_runs(ids)
        else:
            # Repository-less mirror: same rules over the in-memory rows.
            self._cleanup_memory(
                cutoff=bound,
                job_terminal=set(job_terminal),
                run_terminal=set(run_terminal),
                max_scheduler_runs=int(max_scheduler_runs),
            )
        return {
            "deleted_jobs": deleted_jobs,
            "deleted_runs": deleted_runs,
            "kept_active_runs": kept_active_runs,
            "retention_days": int(retention_days),
        }

    def _cleanup_memory(
        self,
        *,
        cutoff: str,
        job_terminal: set[str],
        run_terminal: set[str],
        max_scheduler_runs: int,
    ) -> None:
        for job_id in [
            job_id
            for job_id, record in self._records.items()
            if record.status in job_terminal
            and (record.end_time or record.start_time) < cutoff
        ]:
            self._records.pop(job_id, None)
            self._journal.pop(job_id, None)
        runs = sorted(self._runs.values(), key=lambda item: str(item.get("started_at", "")))
        newest_per_task: dict[str, str] = {}
        for row in reversed(runs):
            newest_per_task.setdefault(str(row.get("task", "")), str(row.get("run_id", "")))
        for row in runs:
            if row.get("status") in run_terminal and (
                row.get("finished_at") or row.get("started_at")
            ) < cutoff and str(row.get("run_id")) not in newest_per_task.values():
                self._runs.pop(str(row["run_id"]), None)
        remaining = sorted(self._runs.values(), key=lambda item: str(item.get("started_at", "")))
        excess = len(remaining) - max_scheduler_runs
        if excess > 0:
            for row in remaining[:excess]:
                if row.get("status") in run_terminal:
                    self._runs.pop(str(row["run_id"]), None)

    # ------------------------------------------------------------------
    # API DTO projection
    # ------------------------------------------------------------------

    @staticmethod
    def summary_dto(record: HistoryRecord) -> dict[str, Any]:
        """Project one record into a JobSummaryDTO-shaped dict."""
        return {
            "job_id": record.job_id,
            "task_type": record.task_type,
            "status": record.status,
            "start_time": record.start_time,
            "end_time": record.end_time,
            "model": record.model,
            "action_count": len(record.proposed_actions),
        }

    @staticmethod
    def detail_dto(record: HistoryRecord) -> dict[str, Any]:
        """Project one record into a JobDetailDTO-shaped dict."""
        return {
            "job_id": record.job_id,
            "task_type": record.task_type,
            "status": record.status,
            "start_time": record.start_time,
            "end_time": record.end_time,
            "model": record.model,
            "prompt_version": record.prompt_version,
            "permission_level": record.permission_level,
            "files_read": record.files_read,
            "proposed_actions": record.proposed_actions,
            "executed_actions": record.executed_actions,
            "diff": record.diff,
            "before_hash": record.before_hash,
            "after_hash": record.after_hash,
            "error": record.error,
        }


__all__ = ["HistoryPage", "HistoryRecord", "HistoryService", "JournalEntry"]
