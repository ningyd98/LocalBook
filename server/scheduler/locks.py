"""Process-local task locks for the M8 scheduler (PLAN-M8 §5.4/§8).

One non-blocking lock per task id guarantees ``max_instances=1`` inside this
process (the plan explicitly does not provide cross-process/distributed
locking).  ``leases`` records who holds what for diagnostics; stale leases
are purely informational here — crash recovery is handled by the startup
scan of derived rows, not by process memory.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager

from .errors import JobInProgress


class TaskLocks:
    """Per-task non-reentrant locks with readable owners."""

    def __init__(self) -> None:
        self._locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
        self._owners: dict[str, str] = {}
        self._guard = threading.RLock()

    def is_locked(self, task: str) -> bool:
        with self._guard:
            return task in self._owners

    def owner(self, task: str) -> str | None:
        with self._guard:
            return self._owners.get(task)

    @contextmanager
    def acquire(self, task: str, run_id: str) -> Iterator[None]:
        """Acquire or raise :class:`JobInProgress`; release on exit."""
        lock = self._locks[task]
        acquired = lock.acquire(blocking=False)
        if not acquired:
            raise JobInProgress(
                "The task already has an active run",
                meta={"task": task, "active_run_id": self.owner(task)},
            )
        with self._guard:
            self._owners[task] = run_id
        try:
            yield
        finally:
            with self._guard:
                self._owners.pop(task, None)
            lock.release()

    def snapshot(self) -> dict[str, str]:
        with self._guard:
            return dict(self._owners)


__all__ = ["TaskLocks"]
