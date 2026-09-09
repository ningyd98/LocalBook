"""Scheduler service facade for M8 (PLAN-M8 §5.4/§5.8/§6.1).

Owns: backend lifecycle (APScheduler preferred, asyncio fallback), static
registration, run records, per-task gating, job timeout / stale marking,
retention triggering and the read-only status/run/recovery views.  It never
implements business logic: Daily/Weekly delegate to the M7 chain through
:class:`JobRunner`; the index job delegates to the read-only checker.  Manual
and scheduled triggers share one pipeline.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import threading
import uuid
from collections.abc import Awaitable, Callable
from concurrent.futures import Future
from datetime import timedelta
from functools import partial
from typing import Any
from urllib.parse import urlparse

from server.actions.schemas import ActionType
from server.agents.service import AgentJobService
from server.config import HistorySettings, SchedulerSettings, ServerSettings
from server.history.service import HistoryService
from server.index.service import DerivedIndexService
from server.policies.errors import JobNotFound
from server.recovery.scanner import RecoveryScanner
from server.recovery.service import RecoveryService
from server.vault.service import VaultService

from .apscheduler_backend import APSchedulerBackend
from .asyncio_backend import AsyncioBackend
from .cleanup import CleanupService
from .clock import Clock, SystemClock, iso_utc, parse_iso
from .cron import next_cron_fire
from .errors import (
    JobInProgress,
    JobTimeout,
    RecoveryNotSafe,
    RecoveryRequired,
    SchedulerConfigInvalid,
    SchedulerDisabled,
    SchedulerErrorCode,
    SchedulerUnavailable,
    UnknownTask,
)
from .index_job import IndexConsistencyChecker
from .locks import TaskLocks
from .models import SchedulerRun, SchedulerRunStatus, to_row
from .registry import all_task_statuses, build_definitions
from .runner import JobRunner, RunOutcome

logger = logging.getLogger("localnote.scheduler")

# Fixed copy surfaced (status + startup log) when a non-loopback host is
# combined with a CORS allow-list that cannot serve a LAN client.
_CORS_ADVICE_TEXT = (
    "host is not loopback: set an explicit LOCALNOTE_SERVER__CORS_ORIGINS "
    "allow-list (currently empty, contains '*' or only loopback origins), e.g. "
    'LOCALNOTE_SERVER__CORS_ORIGINS=\'["http://<trusted-lan-client>:5173"]\''
)
_LOOPBACK_ORIGIN_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_ACTIVE_RUN_STATUSES = (SchedulerRunStatus.QUEUED.value, SchedulerRunStatus.RUNNING.value)
_WATCHDOG_TICK_SECONDS = 5.0


def _is_loopback_host(host: str | None) -> bool:
    """True when the configured listen host is a loopback address/literal.

    ``0.0.0.0``/``::`` (unspecified), plain LAN addresses (``192.168.x``) and
    non-loopback hostnames are all treated as exposed (I2 fix: any
    non-loopback host must warn, not only the two wildcard bindings).
    """
    host = (host or "127.0.0.1").strip().lower().rstrip(".")
    if not host or host in {"localhost", "::1"} or host.startswith("127."):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        # Not an IP literal: cannot prove loopback, so treat it as exposed.
        return False


def _cors_is_loopback_only(origins: list[str] | None) -> bool:
    """True when origins are empty, contain ``*``, or only reference loopback
    hosts (the default whitelist shape) — none of which is an explicit CORS
    allow-list for a LAN client."""
    raw = [str(item).strip() for item in (origins or []) if str(item).strip()]
    if not raw:
        return True
    if "*" in raw:
        return True
    for origin in raw:
        lowered = origin.strip().lower()
        try:
            hostname = urlparse(lowered).hostname or lowered
        except Exception:  # pragma: no cover - defensive parse
            hostname = lowered
        if hostname not in _LOOPBACK_ORIGIN_HOSTS:
            return False
    return True


class SchedulerService:
    """One in-process scheduler over one settings snapshot."""

    def __init__(
        self,
        *,
        settings: SchedulerSettings,
        server: ServerSettings | None = None,
        history: HistoryService | None = None,
        agent_service: AgentJobService | None = None,
        recovery: RecoveryService | None = None,
        vault: VaultService | None = None,
        index_service: DerivedIndexService | None = None,
        index_auto_rebuild: bool = False,
        history_settings: HistorySettings | None = None,
        clock: Clock | None = None,
        backend_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.settings = settings
        self.server = server or ServerSettings()
        self.clock = clock or SystemClock()
        self.history = history
        self.agent_service = agent_service
        self.recovery = recovery
        self.vault = vault
        self.index_service = index_service
        self.history_settings = history_settings or HistorySettings()
        self.timezone = settings.timezone
        self._backend: Any = None
        self._backend_factory = backend_factory
        self._started = False
        self._reconfiguring = False
        self._worker_loop: asyncio.AbstractEventLoop | None = None
        self._worker_thread: threading.Thread | None = None
        self._worker_stop = threading.Event()
        self._lock = threading.RLock()
        self._active_runs: dict[str, str] = {}  # run_id -> task
        self.runner = self._build_runner(index_auto_rebuild=index_auto_rebuild)
        self.cleanup = CleanupService(
            history,
            retention_days=self.history_settings.retention_days,
            cleanup_enabled=self.history_settings.cleanup_enabled,
            cleanup_interval_hours=self.history_settings.cleanup_interval_hours,
            max_scheduler_runs=self.history_settings.max_scheduler_runs,
            clock=clock,
        )
        self._locks = TaskLocks()

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    def _build_runner(self, *, index_auto_rebuild: bool) -> JobRunner:
        checker = IndexConsistencyChecker(
            self.vault,
            self.index_service,
            auto_rebuild=index_auto_rebuild,
        )
        level2_actions = {ActionType(name) for name in self.settings.level2_auto_actions}
        return JobRunner(
            self.agent_service,
            index_checker=checker,
            level2_auto_enabled=self.settings.level2_auto_enabled,
            level2_auto_actions=level2_actions,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self.settings.enabled

    @property
    def started(self) -> bool:
        with self._lock:
            return self._started

    def start(self) -> dict[str, Any]:
        """Register definitions and start the backend (idempotent)."""
        with self._lock:
            if self._started:
                return {"running": True, "backend": self._backend_name()}
            if not self.enabled:
                return {"running": False, "backend": self._backend_name()}
            self._start_worker()
            backend = self._make_backend()
            for definition in build_definitions(self.settings):
                backend.add(definition, partial(self._on_trigger, definition.job_id))
            backend.start()
            self._backend = backend
            self._started = True
            if self._worker_loop is not None:
                asyncio.run_coroutine_threadsafe(self._watchdog_loop(), self._worker_loop)
        logger.info(
            "scheduler started backend=%s timezone=%s jobs=%s",
            self._backend_name(),
            self.timezone,
            backend.registered_ids(),
        )
        return {"running": True, "backend": self._backend_name()}

    def stop(self, *, wait: bool = True, timeout: float = 5.0) -> None:
        """Bounded, idempotent stop; never calls ``sys.exit`` and never stops
        health/Vault/editor/AI/manual jobs (it only stops new triggers)."""
        with self._lock:
            if not self._started:
                return
            self._started = False
            backend, self._backend = self._backend, None
        if backend is not None:
            try:
                backend.stop(wait=False)
            except Exception:  # pragma: no cover - defensive
                logger.exception("scheduler backend stop failed")
        # Bounded drain: give short in-flight runs a grace window, then stop
        # the worker loop; anything still active is flagged so the next boot
        # scan can diagnose it (no auto-write/rollback).
        interrupted = self._drain_and_stop_worker(wait=wait, timeout=timeout)
        if interrupted:
            logger.warning(
                "scheduler shutdown flagged %d run(s) as recovery_required",
                interrupted,
            )
        logger.info("scheduler stopped")

    def _drain_and_stop_worker(self, *, wait: bool, timeout: float) -> int:
        import time as _time

        deadline = _time.monotonic() + timeout if wait else _time.monotonic()
        while wait and self._active_run_ids() and _time.monotonic() < deadline:
            _time.sleep(0.02)
        self._stop_worker_now()
        return self._mark_interrupted_after_shutdown()

    def _active_run_ids(self) -> list[str]:
        with self._lock:
            return list(self._active_runs)

    def _mark_interrupted_after_shutdown(self) -> int:
        with self._lock:
            leftover = list(self._active_runs)
            self._active_runs.clear()
        marked = 0
        now = self.clock.now_iso()
        for run_id in leftover:
            current = self._get_run(run_id)
            if current is None or current.get("status") not in _ACTIVE_RUN_STATUSES:
                continue
            self._update_status(
                run_id,
                SchedulerRunStatus.RECOVERY_REQUIRED,
                error_code="process_interrupted",
                message="scheduler shut down while the run was active",
                finished_at=now,
            )
            marked += 1
        return marked

    # ------------------------------------------------------------------
    # Backend selection (APScheduler preferred)
    # ------------------------------------------------------------------

    def _backend_name(self) -> str:
        if self._backend_factory is not None:
            return getattr(self._backend_factory(), "name", "injected")
        from .apscheduler_backend import _APSCHEDULER_AVAILABLE

        return "apscheduler" if _APSCHEDULER_AVAILABLE else "asyncio"

    def _make_backend(self) -> Any:
        if self._backend_factory is not None:
            return self._backend_factory()
        from .apscheduler_backend import _APSCHEDULER_AVAILABLE

        if _APSCHEDULER_AVAILABLE:
            return APSchedulerBackend(self.settings, clock=self.clock)
        return AsyncioBackend(clock=self.clock)

    @property
    def backend(self) -> Any:
        return self._backend

    def backend_status(self) -> dict[str, Any]:
        backend = self._backend
        if backend is None:
            return {"running": False, "backend": self._backend_name(), "jobs": []}
        try:
            return backend.status()
        except Exception:  # pragma: no cover - defensive
            return {"running": False, "backend": self._backend_name(), "jobs": []}

    # ------------------------------------------------------------------
    # Worker event loop (single executor for every run pipeline)
    # ------------------------------------------------------------------

    def _start_worker(self) -> None:
        with self._lock:
            if self._worker_thread is not None:
                return
            self._worker_stop.clear()
            loop = asyncio.new_event_loop()

            def run_loop() -> None:
                asyncio.set_event_loop(loop)
                loop.run_forever()
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
                loop.close()

            thread = threading.Thread(
                target=run_loop,
                name="localnote-scheduler-worker",
                daemon=True,
            )
            self._worker_loop = loop
            self._worker_thread = thread
            thread.start()

    def _stop_worker_now(self) -> None:
        """Stop the worker loop promptly (bounded join); in-flight coroutines
        are cancelled at the loop teardown step in the worker thread."""
        with self._lock:
            loop = self._worker_loop
            thread = self._worker_thread
            self._worker_loop = None
            self._worker_thread = None
        if loop is None or thread is None:
            return
        self._worker_stop.set()
        try:
            loop.call_soon_threadsafe(loop.stop)
        except Exception:  # pragma: no cover - defensive
            pass
        try:
            thread.join(timeout=1.0)
        except Exception:  # pragma: no cover - defensive
            pass

    def _submit(self, coro: Awaitable[Any]) -> Future[Any]:
        loop = self._worker_loop
        if loop is None or not loop.is_running():
            raise SchedulerUnavailable("scheduler worker is not running")
        return asyncio.run_coroutine_threadsafe(coro, loop)

    # ------------------------------------------------------------------
    # Triggers (scheduled + manual share this path)
    # ------------------------------------------------------------------

    def _on_trigger(self, job_id: str) -> None:
        """Backend/thread-safe entry for a scheduled fire (no wait).

        The pipeline is dispatched exactly like a manual trigger; if the task
        is already active/recovering, a ``skipped_duplicate`` marker run is
        recorded instead (``_begin_run`` returns None).
        """
        if not self.started:
            return
        try:
            run_id = self._begin_run(task=job_id, trigger="scheduled")
            if run_id is not None and self.started:
                self._submit(self._pipeline(run_id=run_id, task=job_id, body={}))
        except Exception:  # noqa: BLE001 - scheduled failures are recorded
            logger.exception("scheduled trigger failed for %s", job_id)

    def arun_task(
        self,
        task: str,
        body: dict[str, Any] | None = None,
        *,
        bypass_recovery: bool = False,
    ) -> Awaitable[dict[str, Any]]:
        """Async manual trigger; awaits completion with the job timeout.

        ``confirm`` truly participates (S2): an unconfirmed manual request
        (``confirm`` falsy/missing) can never auto-execute — even when the
        server Level-2 switch is on it degrades to the Level-1 preview, and
        writes only ever happen through the M7 ``accept`` endpoint.
        ``bypass_recovery`` is the internal retry_preview path: it opens a
        new run even when the task's previous run is ``recovery_required``
        (that previous row is left untouched).
        """
        body = body or {}
        if not body.get("confirm", False):
            body = {**body, "auto_level2": False}

        async def _manual() -> dict[str, Any]:
            run_id = self._begin_run(
                task=task,
                trigger="manual",
                body=body,
                bypass_recovery=bypass_recovery,
            )
            outcome = await self._pipeline(run_id=run_id, task=task, body=body)
            return self._response(run_id, outcome)

        if not self.started:
            return _manual()
        run_id = self._begin_run(
            task=task, trigger="manual", body=body, bypass_recovery=bypass_recovery
        )
        future = self._submit(self._pipeline(run_id=run_id, task=task, body=body))

        async def _await_manual() -> dict[str, Any]:
            try:
                await asyncio.wait_for(
                    asyncio.wrap_future(future),
                    timeout=self.settings.job_timeout_seconds,
                )
            except TimeoutError:
                self._mark_timed_out(run_id)
                raise JobTimeout(
                    "Scheduler job timed out",
                    meta={"run_id": run_id, "task": task},
                ) from None
            outcome = future.result()
            return self._response(run_id, outcome)

        return _await_manual()

    def run_task(
        self,
        task: str,
        body: dict[str, Any] | None = None,
        *,
        bypass_recovery: bool = False,
    ) -> dict[str, Any]:
        """Synchronous manual trigger (unit tests / non-async callers)."""
        return asyncio.run(self.arun_task(task, body, bypass_recovery=bypass_recovery))

    # ------------------------------------------------------------------
    # Run pipeline
    # ------------------------------------------------------------------

    def pause_for_reconfigure(self) -> bool:
        """Atomically block triggers only when no pipeline still owns this runtime."""
        with self._lock:
            if self._active_runs or self._locks.snapshot():
                return False
            self._reconfiguring = True
            return True

    def resume_after_reconfigure(self) -> None:
        with self._lock:
            self._reconfiguring = False

    def _begin_run(self, **kwargs: Any) -> str | None:
        with self._lock:
            if self._reconfiguring:
                if kwargs.get("trigger") == "scheduled":
                    return None
                raise SchedulerUnavailable("Workspace configuration is changing")
            return self._begin_run_unlocked(**kwargs)

    def _begin_run_unlocked(
        self,
        *,
        task: str,
        trigger: str,
        body: dict[str, Any] | None = None,
        bypass_recovery: bool = False,
    ) -> str | None:
        """Gate + create the run row; returns its id (``None`` when a scheduled
        trigger was skipped because the task is busy/recovering).  Manual
        triggers raise typed errors instead of returning ``None``.

        ``bypass_recovery`` is used by ``retry_preview`` (I1): a manual,
        explicitly requested retry opens a fresh run even when the previous
        run of the task is ``recovery_required``, and leaves that old row's
        ``recovery_required`` marking untouched.
        """
        if task not in ("daily_organizer", "weekly_review", "index_consistency"):
            raise UnknownTask("Unknown scheduler task", meta={"task": task})
        if not self.enabled:
            raise SchedulerDisabled("Scheduler is disabled", meta={"task": task})
        if not self.started:
            raise SchedulerUnavailable("Scheduler is not running", meta={"task": task})
        body = body or {}
        now = self.clock.now_iso()
        scheduled_for = body.get("scheduled_for") or now
        last = self._last_run(task)
        if last is not None:
            last_status = last.get("status")
            if last_status == SchedulerRunStatus.RECOVERY_REQUIRED.value:
                if not (trigger == "manual" and bypass_recovery):
                    self._write_skip(
                        task=task,
                        trigger=trigger,
                        status=SchedulerRunStatus.SKIPPED_DUPLICATE,
                        error_code=SchedulerErrorCode.RECOVERY_REQUIRED.value,
                        message="previous run needs explicit recovery",
                        scheduled_for=scheduled_for,
                    )
                    if trigger == "manual":
                        raise RecoveryRequired(
                            "A previous run needs explicit recovery",
                            meta={"task": task, "run_id": last.get("run_id")},
                        )
                    return None  # type: ignore[return-value]
            elif last_status in _ACTIVE_RUN_STATUSES:
                started = parse_iso(str(last.get("started_at") or now))
                age = (self.clock.now_utc() - started).total_seconds()
                if age > self.settings.stale_run_after_seconds:
                    # An orphaned "running" row cannot block the schedule.
                    self._update_status(
                        str(last["run_id"]),
                        SchedulerRunStatus.RECOVERY_REQUIRED,
                        error_code="process_interrupted",
                        message="stale run superseded at startup/trigger",
                    )
                else:
                    self._write_skip(
                        task=task,
                        trigger=trigger,
                        status=SchedulerRunStatus.SKIPPED_DUPLICATE,
                        error_code=(
                            SchedulerErrorCode.RECOVERY_REQUIRED.value
                            if last_status == SchedulerRunStatus.RECOVERY_REQUIRED.value
                            else SchedulerErrorCode.DUPLICATE_RUN.value
                        ),
                        message="the task already has an active run",
                        scheduled_for=scheduled_for,
                    )
                    if trigger == "manual":
                        raise JobInProgress(
                            "The task already has an active run",
                            meta={"task": task, "active_run_id": last.get("run_id")},
                        )
                    return None  # type: ignore[return-value]
        run_id = str(uuid.uuid4())
        row = to_row(
            SchedulerRun(
                run_id=uuid.UUID(run_id),
                task=task,
                trigger=trigger,
                idempotency_key=f"{task}:{scheduled_for}",
                status=SchedulerRunStatus.RUNNING,
                scheduled_for=scheduled_for,
                started_at=now,
                finished_at=None,
            )
        )
        row["created_at"] = now
        self._save_run(row)
        with self._lock:
            self._active_runs[run_id] = task
        return run_id

    async def _pipeline(
        self,
        *,
        run_id: str,
        task: str,
        body: dict[str, Any],
    ) -> RunOutcome:
        """Execute one task body and persist the final run state."""
        outcome: RunOutcome
        try:
            outcome = await self.runner.arun(
                task,
                scope=body.get("scope"),
                request_auto_level2=body.get("auto_level2"),
            )
        except Exception:  # noqa: BLE001 - bounded conversion (S3)
            logger.exception(
                "scheduler run failed unexpectedly task=%s run_id=%s",
                task,
                run_id,
            )
            outcome = RunOutcome(
                status=SchedulerRunStatus.FAILED,
                error_code=SchedulerErrorCode.SCHEDULER_UNAVAILABLE.value,
                message="an unexpected scheduler error occurred (details logged)",
            )
        self._finalize(run_id, outcome)
        with self._lock:
            self._active_runs.pop(run_id, None)
        try:
            self.cleanup.run_if_due()
        except Exception:  # pragma: no cover - diagnostics only
            logger.exception("history cleanup trigger failed")
        return outcome

    def _finalize(self, run_id: str, outcome: RunOutcome) -> None:
        current = self._get_run(run_id)
        if current is None:
            return
        now = self.clock.now_iso()
        if current.get("status") == SchedulerRunStatus.TIMED_OUT.value:
            self._update_status(
                run_id,
                SchedulerRunStatus.RECOVERY_REQUIRED,
                agent_job_id=outcome.agent_job_id,
                error_code="job_timeout",
                message="job finished after the timeout mark; verify state",
                finished_at=now,
            )
            return
        self._update_status(
            run_id,
            outcome.status,
            agent_job_id=outcome.agent_job_id,
            error_code=outcome.error_code,
            message=outcome.message,
            finished_at=now,
        )

    def _mark_timed_out(self, run_id: str) -> None:
        self._update_status(
            run_id,
            SchedulerRunStatus.TIMED_OUT,
            error_code=SchedulerErrorCode.JOB_TIMEOUT.value,
            message="exceeded the job timeout",
            finished_at=self.clock.now_iso(),
        )

    def _write_skip(
        self,
        *,
        task: str,
        trigger: str,
        status: SchedulerRunStatus,
        error_code: str | None,
        message: str,
        scheduled_for: str | None,
    ) -> None:
        now = self.clock.now_iso()
        row = to_row(
            SchedulerRun(
                run_id=uuid.uuid4(),
                task=task,
                trigger=trigger,
                idempotency_key=f"{task}:{scheduled_for or now}",
                status=status,
                scheduled_for=scheduled_for,
                started_at=now,
                finished_at=now,
                error_code=error_code,
                message=message,
            )
        )
        row["created_at"] = now
        self._save_run(row)

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _save_run(self, row: dict[str, object]) -> None:
        if self.history is None:
            raise SchedulerUnavailable("history is unavailable")
        self.history.save_run(row)

    def _get_run(self, run_id: str) -> dict[str, object] | None:
        if self.history is None:
            return None
        return self.history.get_run(run_id)

    def _update_status(
        self,
        run_id: str,
        status: SchedulerRunStatus,
        *,
        agent_job_id: str | None = None,
        error_code: str | None = None,
        message: str | None = None,
        finished_at: str | None = None,
    ) -> None:
        current = self._get_run(run_id)
        if current is None:
            return
        updated = dict(current)
        updated["status"] = status.value
        if agent_job_id is not None:
            updated["agent_job_id"] = agent_job_id
        if error_code is not None:
            updated["error_code"] = error_code
        if message is not None:
            updated["message"] = message
        if finished_at is not None:
            updated["finished_at"] = finished_at
        self._save_run(updated)

    def _last_run(self, task: str) -> dict[str, object] | None:
        if self.history is None:
            return None
        page = self.history.page_runs(task=task, limit=1, offset=0)
        items = page.get("items") or []
        return items[0] if items else None

    # ------------------------------------------------------------------
    # Watchdog (timeouts + stale runs)
    # ------------------------------------------------------------------

    async def _watchdog_loop(self) -> None:
        tick = max(1.0, min(_WATCHDOG_TICK_SECONDS, self.settings.job_timeout_seconds / 4.0))
        while self.started:
            try:
                await asyncio.sleep(tick)
                self._watchdog_pass()
            except asyncio.CancelledError:
                return
            except Exception:  # pragma: no cover - defensive
                logger.exception("scheduler watchdog failed")

    def _watchdog_pass(self) -> None:
        if self.history is None:
            return
        now = self.clock.now_utc()
        for row in self.history.runs_with_status(_ACTIVE_RUN_STATUSES):
            run_id = str(row["run_id"])
            started = parse_iso(str(row.get("started_at") or ""))
            age = (now - started).total_seconds()
            with self._lock:
                alive = run_id in self._active_runs
            if not alive and age > self.settings.stale_run_after_seconds:
                self._update_status(
                    run_id,
                    SchedulerRunStatus.RECOVERY_REQUIRED,
                    error_code="process_interrupted",
                    message="abandoned running run flagged by watchdog",
                    finished_at=iso_utc(now),
                )
            elif alive and age > self.settings.job_timeout_seconds:
                self._mark_timed_out(run_id)

    # ------------------------------------------------------------------
    # Status / listing / responses
    # ------------------------------------------------------------------

    def _compute_next_run(self, job: dict[str, object]) -> str | None:
        trigger = str(job["trigger"])
        expression = str(job["expression"])
        if trigger == "interval":
            return iso_utc(
                self.clock.now_utc() + timedelta(hours=max(1, int(expression)))
            )
        found = next_cron_fire(expression, self.timezone, self.clock.now_utc())
        return iso_utc(found) if found is not None else None

    def network_warning(self) -> bool:
        """True when the listen host is not loopback (I2: any non-loopback
        host — ``0.0.0.0``, ``::``, ``192.168.x``, … — is treated as exposed)."""
        return not _is_loopback_host(self.server.host)

    def network_advice(self) -> str | None:
        """Fixed CORS advice when an exposed host lacks an explicit CORS
        allow-list (empty / ``*`` / loopback-only origins); None otherwise."""
        if not self.network_warning():
            return None
        if _cors_is_loopback_only(self.server.cors_origins):
            return _CORS_ADVICE_TEXT
        return None

    def status(self) -> dict[str, Any]:
        backend_state = self.backend_status()
        jobs: list[dict[str, Any]] = []
        for definition in all_task_statuses(self.settings):
            job_id = str(definition["id"])
            last = self._last_run(job_id)
            enabled = bool(definition["enabled"]) and self.enabled
            next_local = self._compute_next_run(definition) if enabled else None
            jobs.append(
                {
                    "id": job_id,
                    "enabled": enabled,
                    "trigger": definition["trigger"],
                    "next_run_at": next_local,
                    "last_status": str(last.get("status")) if last else None,
                    "last_run_id": str(last.get("run_id")) if last else None,
                    "last_message": str(last.get("message")) if last else None,
                    "last_finished_at": str(last.get("finished_at")) if last else None,
                }
            )
        active_runs = 0
        recovery_required = 0
        if self.history is not None:
            active_runs = len(
                self.history.runs_with_status(_ACTIVE_RUN_STATUSES)
            )
            recovery_required = self.history.recovery_required_count()
        return {
            "enabled": self.enabled,
            "running": bool(self.started and backend_state.get("running")),
            "backend": str(backend_state.get("backend") or self._backend_name()),
            "degraded": bool(
                backend_state.get("degraded_reason")
                or (backend_state.get("available") is False)
            ),
            "degraded_reason": backend_state.get("degraded_reason"),
            "timezone": self.timezone,
            "network_exposure_warning": self.network_warning(),
            "network_exposure_advice": self.network_advice(),
            "jobs": jobs,
            "active_runs": active_runs,
            "recovery_required": recovery_required,
            "history_retention_days": self.history_settings.retention_days,
        }

    def list_runs(
        self, *, limit: int = 20, offset: int = 0, task: str | None = None
    ) -> dict[str, Any]:
        if self.history is None:
            raise SchedulerUnavailable("history is unavailable")
        return self.history.page_runs(limit=limit, offset=offset, task=task)

    def _response(self, run_id: str, outcome: RunOutcome | None = None) -> dict[str, Any]:
        run = self._get_run(run_id) or {}
        policy = None
        detail = None
        if outcome is not None:
            policy = outcome.policy
            detail = outcome.detail
        return {
            "run_id": run_id,
            "task": run.get("task"),
            "status": run.get("status"),
            "trigger": run.get("trigger"),
            "agent_job_id": run.get("agent_job_id"),
            "scheduled_for": run.get("scheduled_for"),
            "started_at": run.get("started_at"),
            "finished_at": run.get("finished_at"),
            "policy": policy,
            "error_code": run.get("error_code"),
            "message": run.get("message"),
            "detail": detail,
        }

    # ------------------------------------------------------------------
    # Explicit recovery (diagnose / rollback_if_safe / retry_preview)
    # ------------------------------------------------------------------

    def recovery_action(self, run_id: str, action: str) -> dict[str, Any]:
        if action == "retry_preview":
            run = self._require_run(run_id)
            task = str(run.get("task") or "")
            # I1: bypass the _last_run recovery gate so a fresh preview run is
            # created (the old recovery_required row is left untouched).
            return self.run_task(
                task,
                {"confirm": True, "auto_level2": False},
                bypass_recovery=True,
            )
        run = self._require_run(run_id)
        task = str(run.get("task") or "")
        agent_job_id = run.get("agent_job_id")
        if action == "diagnose":
            diagnosis = None
            if agent_job_id and self.recovery is not None:
                diagnosis = self.recovery.diagnose(str(agent_job_id)).model_dump()
            return {
                "run_id": run_id,
                "task": task,
                "status": run.get("status"),
                "agent_job_id": agent_job_id,
                "diagnosis": diagnosis,
            }
        if action == "rollback_if_safe":
            if not agent_job_id or self.recovery is None:
                raise RecoveryNotSafe(
                    "No agent job is attached to this run",
                    meta={"run_id": run_id},
                )
            result = self.recovery.rollback_if_safe(str(agent_job_id))
            if result.status.value == "conflict":
                raise RecoveryNotSafe(
                    "Recovery is not safe; files changed externally",
                    meta={"run_id": run_id, "conflict_paths": result.conflict_paths},
                )
            if result.status.value == "rollback_failed":
                raise RecoveryNotSafe(
                    "Recovery rollback failed",
                    meta={"run_id": run_id, "conflict_paths": result.conflict_paths},
                )
            if result.status.value == "rolled_back":
                self._update_status(
                    run_id,
                    SchedulerRunStatus.FAILED,
                    error_code="recovery_rolled_back",
                    message=result.error or "explicit rollback completed",
                    finished_at=self.clock.now_iso(),
                )
                return {
                    "run_id": run_id,
                    "task": task,
                    "status": "rolled_back",
                    "restored": result.restored,
                    "message": result.error,
                }
            raise RecoveryNotSafe(
                "Recovery is not safe for this run",
                meta={"run_id": run_id, "detail": result.status.value},
            )
        raise SchedulerConfigInvalid(
            f"unknown recovery action: {action}", meta={"run_id": run_id}
        )

    def _require_run(self, run_id: str) -> dict[str, object]:
        run = self._get_run(run_id)
        if run is None:
            raise JobNotFound("run not found", meta={"run_id": run_id})
        return run

    def scan_recovery(self, *, mark: bool = False) -> dict[str, Any]:
        """Startup crash scan (default read-only); ``mark=True`` flags rows."""
        scanner = RecoveryScanner(self.history, clock=self.clock)
        if mark:
            return scanner.mark_recovery_required(self.history)
        return scanner.scan(self.history)


__all__ = ["SchedulerService"]
