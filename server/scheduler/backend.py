"""Scheduler backend abstraction for M8 (PLAN-M8 §5.1).

The backend owns *when* a trigger fires; it never knows about business
logic.  A registered callback is zero-arg (the scheduler service builds the
closure with the stable job id).  Backends must be start/stop idempotent and
never spawn more than one instance of a job (max_instances=1).

Manual triggers do not round-trip through a scheduling engine: they reuse the
same service handler via :class:`server.scheduler.service.SchedulerService`,
so the backend protocol's ``run_now`` is a thin convenience that fires the
registered callback once through the backend's own dispatch path.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from .clock import Clock, SystemClock, iso_utc
from .models import JobDefinition, RunHandle, SchedulerRunStatus


class SchedulerBackend(Protocol):
    name: str

    def start(self) -> None: ...

    def stop(self, *, wait: bool = True, timeout: float = 5.0) -> None: ...

    def add(self, definition: JobDefinition, callback: Callable[[], None]) -> None: ...

    def remove(self, job_id: str) -> None: ...

    def status(self) -> dict[str, Any]: ...

    def run_now(self, job_id: str) -> RunHandle: ...


class BaseBackend:
    """Shared bookkeeping for single-process backends."""

    name = "base"
    degraded_reason: str | None = None

    def __init__(self, clock: Clock | None = None) -> None:
        self.clock = clock or SystemClock()
        self._lock = threading.RLock()
        self._jobs: dict[str, tuple[JobDefinition, Callable[[], None]]] = {}
        self._running = False

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    def add(self, definition: JobDefinition, callback: Callable[[], None]) -> None:
        with self._lock:
            if definition.job_id in self._jobs:
                raise ValueError(f"duplicate scheduler job id: {definition.job_id}")
            self._jobs[definition.job_id] = (definition, callback)

    def remove(self, job_id: str) -> None:
        with self._lock:
            self._jobs.pop(job_id, None)

    def registered_ids(self) -> list[str]:
        with self._lock:
            return sorted(self._jobs)

    def _fire(self, job_id: str) -> None:
        with self._lock:
            callback = self._jobs[job_id][1]
        callback()

    def run_now(self, job_id: str) -> RunHandle:
        self._fire(job_id)
        return RunHandle(run_id=str(uuid.uuid4()), status=SchedulerRunStatus.QUEUED)

    def status(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "backend": self.name,
            "degraded_reason": self.degraded_reason,
            "jobs": self._job_states(),
        }

    def _job_states(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "job_id": definition.job_id,
                    "enabled": definition.enabled,
                    "next_run_at": None,
                    "last_run_at": None,
                }
                for definition, _callback in self._jobs.values()
            ]


def utc_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return iso_utc(value)


def now_utc() -> datetime:
    return datetime.now(UTC)


def format_next(next_run: datetime | None) -> str | None:
    return iso_utc(next_run) if next_run is not None else None


__all__ = ["BaseBackend", "SchedulerBackend", "format_next", "now_utc"]
