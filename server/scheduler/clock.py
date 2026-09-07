"""Timezone-aware injectable clock for the M8 scheduler (PLAN-M8 §5.2).

The real process clock is UTC-based; timezone conversions only ever happen
for *display* (next-run in the configured zone) or for backend cron math.
Tests inject a fake clock so schedule tests never depend on wall-clock time
or real sleeps.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol
from zoneinfo import ZoneInfo


def parse_iso(value: str) -> datetime:
    """Parse a stored UTC ISO timestamp (naive or aware) as aware UTC."""
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def iso_utc(value: datetime | None = None) -> str:
    if value is None:
        value = datetime.now(UTC)
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


class Clock(Protocol):
    def now_utc(self) -> datetime: ...
    def now_iso(self) -> str: ...
    def now_tz(self, timezone: str) -> datetime: ...


class SystemClock:
    """Wall-clock implementation (UTC timestamps, zoneinfo conversions)."""

    def now_utc(self) -> datetime:
        return datetime.now(UTC)

    def now_iso(self) -> str:
        return iso_utc(datetime.now(UTC))

    def now_tz(self, timezone: str) -> datetime:
        return datetime.now(UTC).astimezone(ZoneInfo(timezone))


__all__ = ["Clock", "SystemClock", "iso_utc", "parse_iso"]
