"""APScheduler BackgroundScheduler adapter for M8 (PLAN-M8 §5.1/§5.4).

Preferred single-process backend: timezone-aware cron/interval triggers with
``max_instances=1``, ``coalesce`` and ``misfire_grace_time`` — exactly the
local-task semantics the plan requires.  The adapter contains no business
code: APScheduler jobs only forward to the registered zero-arg callback.

Cron day-of-week semantics: configuration, ``server.scheduler.cron`` and the
status next-run computation all speak Vixie cron (``0``/``7`` = Sunday),
while APScheduler's ``DayOfWeekField`` speaks Python ``date.weekday()``
(``0`` = Monday).  :meth:`_trigger_for` therefore maps the fifth crontab
field Vixie → APScheduler before handing it to ``CronTrigger`` so the
preferred backend and the asyncio fallback/status always agree on the same
fire day (fix for the B1 audit finding).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from server.config import SchedulerSettings

from .backend import BaseBackend, format_next
from .clock import Clock
from .models import JobDefinition

try:  # pragma: no cover - exercised at import time per environment
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger
except Exception:  # pragma: no cover - apscheduler missing -> asyncio fallback
    BackgroundScheduler = None  # type: ignore[assignment,misc]
    CronTrigger = None  # type: ignore[assignment,misc]
    IntervalTrigger = None  # type: ignore[assignment,misc]

_APSCHEDULER_AVAILABLE = BackgroundScheduler is not None


def _vixie_dow_to_apscheduler(token: str) -> str:
    """Translate one Vixie day-of-week crontab token into APScheduler syntax.

    Vixie: ``0``..``7`` where ``0`` and ``7`` both mean Sunday.
    APScheduler: ``0``..``6`` where ``0`` = Monday .. ``6`` = Sunday
    (``date.weekday()`` ordering).  ``*`` and ``*/step`` are expanded to the
    exact weekday set the Vixie field allows (range 0..7 inclusive, matching
    ``server.scheduler.cron``), each value mapped with ``(d + 6) % 7``, then
    re-serialized as an explicit APScheduler list so the two backends always
    agree on which weekdays fire.
    """
    if token == "*":
        return "*"
    if token.startswith("*/"):
        values = range(0, 8, int(token[2:]))
    else:
        values = (int(token),)
    mapped = sorted({(value + 6) % 7 for value in values})
    return ",".join(str(value) for value in mapped)


class APSchedulerBackend(BaseBackend):
    """BackgroundScheduler wrapper; no-op guards when APScheduler is absent."""

    name = "apscheduler"

    def __init__(
        self,
        settings: SchedulerSettings | None = None,
        clock: Clock | None = None,
    ) -> None:
        super().__init__(clock=clock)
        self.settings = settings or SchedulerSettings()
        self._scheduler: Any = None
        self._aps_jobs: dict[str, Any] = {}
        self.degraded_reason = None if _APSCHEDULER_AVAILABLE else (
            "apscheduler import failed; scheduler is not usable"
        )

    @property
    def available(self) -> bool:
        return _APSCHEDULER_AVAILABLE

    # ------------------------------------------------------------------
    # Registration (allowed before start so callers can add() then start())
    # ------------------------------------------------------------------

    def add(self, definition: JobDefinition, callback: Callable[[], None]) -> None:
        super().add(definition, callback)
        if self._scheduler is None:
            return  # will be scheduled on start()
        self._schedule_job(definition, callback)

    def _trigger_for(self, definition: JobDefinition) -> Any:
        timezone = self.settings.timezone if definition.trigger == "cron" else None
        if definition.trigger == "cron":
            if CronTrigger is None:  # pragma: no cover - guard
                raise RuntimeError("APScheduler is not importable")
            fields = str(definition.expression).split()
            # Vixie (config/cron.py/status) -> APScheduler dow mapping (B1).
            fields[4] = _vixie_dow_to_apscheduler(fields[4])
            return CronTrigger.from_crontab(
                " ".join(fields), timezone=timezone or "UTC"
            )
        hours = int(definition.expression)
        if IntervalTrigger is None:  # pragma: no cover - guard
            raise RuntimeError("APScheduler is not importable")
        return IntervalTrigger(hours=hours)

    def _schedule_job(self, definition: JobDefinition, callback: Callable[[], None]) -> None:
        if self._scheduler is None or CronTrigger is None:
            return
        trigger = self._trigger_for(definition)
        job = self._scheduler.add_job(
            callback,
            trigger=trigger,
            id=definition.job_id,
            replace_existing=True,
            coalesce=self.settings.coalesce,
            max_instances=self.settings.max_instances,
            misfire_grace_time=self.settings.misfire_grace_seconds,
        )
        self._aps_jobs[definition.job_id] = job

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        with self._lock:
            if not _APSCHEDULER_AVAILABLE:
                return
            if self._scheduler is not None:
                return
            scheduler = BackgroundScheduler(timezone=str(self.settings.timezone))
            scheduler.start()
            self._scheduler = scheduler
            for _job_id, (definition, _callback) in self._jobs.items():
                self._schedule_job(definition, _callback)
            self._running = True

    def stop(self, *, wait: bool = True, timeout: float = 5.0) -> None:
        with self._lock:
            if self._scheduler is None:
                self._running = False
                return
            scheduler, self._scheduler = self._scheduler, None
            self._aps_jobs.clear()
            self._running = False
        try:
            scheduler.shutdown(wait=wait)
        except Exception:  # pragma: no cover - defensive on shutdown races
            pass

    def remove(self, job_id: str) -> None:
        super().remove(job_id)
        aps_job = self._aps_jobs.pop(job_id, None)
        if aps_job is not None and self._scheduler is not None:
            try:
                aps_job.remove()
            except Exception:  # pragma: no cover - already removed
                pass

    def status(self) -> dict[str, Any]:
        state = super().status()
        jobs: list[dict[str, Any]] = []
        with self._lock:
            for job_id, (definition, _callback) in self._jobs.items():
                next_run: datetime | None = None
                aps_job = self._aps_jobs.get(job_id)
                if aps_job is not None:
                    candidate = getattr(aps_job, "next_run_time", None)
                    if candidate is not None:
                        next_run = (
                            candidate
                            if candidate.tzinfo is not None
                            else candidate.replace(tzinfo=UTC)
                        )
                jobs.append(
                    {
                        "job_id": job_id,
                        "enabled": definition.enabled,
                        "next_run_at": format_next(next_run),
                        "last_run_at": None,
                    }
                )
        state["jobs"] = jobs
        state["available"] = self.available
        return state

    # ------------------------------------------------------------------
    # run_now: fire the registered callback once through the local executor
    # ------------------------------------------------------------------

    def run_now(self, job_id: str):
        if self._scheduler is None or not _APSCHEDULER_AVAILABLE:
            return super().run_now(job_id)
        callback = self._jobs[job_id][1]
        job = self._scheduler.add_job(
            callback,
            trigger="date",
            run_date=datetime.now(UTC),
            id=f"{job_id}:manual",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=1,
        )
        from .models import RunHandle, SchedulerRunStatus

        return RunHandle(run_id=job.id, status=SchedulerRunStatus.QUEUED)


__all__ = ["APSchedulerBackend", "_APSCHEDULER_AVAILABLE"]
