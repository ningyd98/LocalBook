"""Retention / stale-run cleanup trigger for M8 (PLAN-M8 §5.6, M8-08).

``CleanupService`` guards how often History retention runs (at most once per
``cleanup_interval_hours``, fake-clock friendly) and reports what was
deleted.  Cleanup itself lives in :class:`server.history.service.HistoryService`;
this module only decides *when* and turns failures into the stable
``history_cleanup_failed`` diagnostic (never a process crash).
"""

from __future__ import annotations

from typing import Any

from server.history.service import HistoryService

from .clock import Clock, SystemClock, parse_iso


class CleanupService:
    """Interval-guarded History retention runner."""

    def __init__(
        self,
        history: HistoryService | None = None,
        *,
        retention_days: int = 30,
        cleanup_enabled: bool = True,
        cleanup_interval_hours: int = 24,
        max_scheduler_runs: int = 1000,
        clock: Clock | None = None,
    ) -> None:
        self.history = history
        self.retention_days = int(retention_days)
        self.cleanup_enabled = bool(cleanup_enabled)
        self.cleanup_interval_hours = int(cleanup_interval_hours)
        self.max_scheduler_runs = int(max_scheduler_runs)
        self.clock = clock or SystemClock()
        self._last_run: str | None = None

    def run_if_due(
        self, *, force: bool = False, now_iso: str | None = None
    ) -> dict[str, Any] | None:
        """Run cleanup when the interval elapsed (or when forced)."""
        now = now_iso or self.clock.now_iso()
        if not force and not self.cleanup_enabled:
            return None
        if not force and self._last_run is not None:
            elapsed_hours = _hours_between(self._last_run, now)
            if elapsed_hours < self.cleanup_interval_hours:
                return None
        return self.run_cleanup(now_iso=now)

    def run_cleanup(self, *, now_iso: str | None = None) -> dict[str, Any]:
        now = now_iso or self.clock.now_iso()
        if self.history is None or not self.history.available:
            return {
                "skipped": True,
                "reason": "history repository unavailable",
                "ran_at": now,
            }
        try:
            result = self.history.cleanup_retention(
                now_iso=now,
                retention_days=self.retention_days,
                max_scheduler_runs=self.max_scheduler_runs,
            )
        except Exception as exc:  # noqa: BLE001 - diagnostics only
            self._last_run = now
            return {
                "skipped": True,
                "error_code": "history_cleanup_failed",
                "message": str(exc).strip()[:500],
                "ran_at": now,
            }
        self._last_run = now
        return {**result, "ran_at": now, "skipped": False}


def _hours_between(start_iso: str, end_iso: str) -> float:
    try:
        delta = parse_iso(end_iso) - parse_iso(start_iso)
        return max(0.0, delta.total_seconds() / 3600.0)
    except Exception:  # pragma: no cover - malformed timestamps
        return float("inf")


__all__ = ["CleanupService"]
