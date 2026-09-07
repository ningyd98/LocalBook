"""Controlled asyncio/thread fallback backend for M8 (PLAN-M8 §5.1).

Used only when APScheduler cannot be imported (or forced for tests): a single
daemon thread wakes at the earliest next-fire deadline computed with
``zoneinfo`` + :mod:`server.scheduler.cron`, fires due jobs once (coalesce:
at most one catch-up fire per slot, never a burst of back-logged runs), and
sleeps again.  Misfires beyond a grace window are not persisted as ``missed``
run rows (no such status exists): like the primary backend, the slot is
simply coalesced/skipped and the run log only records triggers that actually
ran or were gated (see README "M8 Scheduler" notes).  It intentionally makes
no claim of wake-across-suspend guarantees — the same caveat documented for
the primary backend.  ``tick()`` lets tests drive the same dispatch logic
with a fake clock and no real sleeping.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from .backend import BaseBackend, format_next
from .clock import Clock
from .cron import next_cron_fire
from .models import JobDefinition

_MAX_SLEEP_SECONDS = 3600.0


class AsyncioBackend(BaseBackend):
    """Thread + ``Event.wait`` scheduling loop (no third-party scheduler)."""

    name = "asyncio"

    def __init__(self, clock: Clock | None = None) -> None:
        super().__init__(clock=clock)
        self._definitions: dict[str, JobDefinition] = {}
        self._next: dict[str, datetime] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.degraded_reason = (
            "APScheduler is unavailable; using the asyncio/thread fallback scheduler"
        )
        self._last_fired: dict[str, str | None] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def add(self, definition: JobDefinition, callback: Callable[[], None]) -> None:
        super().add(definition, callback)
        with self._lock:
            self._definitions[definition.job_id] = definition
            self._next[definition.job_id] = self._compute_next(definition)

    def remove(self, job_id: str) -> None:
        with self._lock:
            self._definitions.pop(job_id, None)
            self._next.pop(job_id, None)
            self._last_fired.pop(job_id, None)
        super().remove(job_id)

    def _compute_next(self, definition: JobDefinition) -> datetime:
        now = self.clock.now_utc()
        if definition.trigger == "interval":
            hours = max(1, int(definition.expression))
            return now + timedelta(hours=hours)
        found = next_cron_fire(definition.expression, definition.timezone, now)
        if found is None:  # pragma: no cover - validated expressions always match
            return now + timedelta(days=1)
        return found

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        with self._lock:
            if self._thread is not None:
                return
            self._stop.clear()
            self._running = True
            thread = threading.Thread(
                target=self._run_loop,
                name="localnote-scheduler-fallback",
                daemon=True,
            )
            self._thread = thread
        thread.start()

    def _run_loop(self) -> None:
        while not self._stop.is_set():
            now = self.clock.now_utc()
            fired = self.tick(now)
            with self._lock:
                next_times = [value for value in self._next.values() if value is not None]
            if fired:
                continue  # recompute immediately so back-to-back slots fire
            if not next_times:
                delay = _MAX_SLEEP_SECONDS
            else:
                deadline = min(next_times)
                delta = (deadline - now).total_seconds()
                delay = min(max(0.1, delta), _MAX_SLEEP_SECONDS)
            self._stop.wait(timeout=delay)

    def tick(self, now: datetime) -> int:
        """Fire every due job once; returns how many fired (test hook too)."""
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        with self._lock:
            due: list[str] = []
            for job_id, definition in self._definitions.items():
                next_run = self._next.get(job_id)
                if next_run is not None and next_run <= now:
                    due.append(job_id)
                    if definition.trigger == "interval":
                        self._next[job_id] = now + timedelta(
                            hours=max(1, int(definition.expression))
                        )
                    else:
                        found = next_cron_fire(
                            definition.expression, definition.timezone, now
                        )
                        self._next[job_id] = (
                            found if found is not None else now + timedelta(days=1)
                        )
        count = 0
        for job_id in due:
            self._last_fired[job_id] = format_next(now)
            self._fire(job_id)
            count += 1
        return count

    def stop(self, *, wait: bool = True, timeout: float = 5.0) -> None:
        with self._lock:
            thread, self._thread = self._thread, None
            self._running = False
        if thread is None:
            return
        self._stop.set()
        if wait and thread.is_alive():
            thread.join(timeout=timeout)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        state = super().status()
        jobs: list[dict[str, Any]] = []
        with self._lock:
            for job_id, definition in self._definitions.items():
                jobs.append(
                    {
                        "job_id": job_id,
                        "enabled": definition.enabled,
                        "next_run_at": format_next(self._next.get(job_id)),
                        "last_run_at": self._last_fired.get(job_id),
                    }
                )
        state["jobs"] = jobs
        state["available"] = True
        return state


__all__ = ["AsyncioBackend"]
