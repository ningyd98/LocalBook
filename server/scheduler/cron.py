"""Pure cron next-fire computation for the M8 scheduler fallback.

APScheduler ships its own trigger math and is the primary backend; this
module is only used by the degraded :mod:`asyncio` backend (or tests) when
APScheduler cannot be imported.  The supported expression subset is the
strict bounded one from ``server.config`` (5 fields of int / ``*`` /
``*/step``); ``zoneinfo`` handles timezone conversion and the standard
Vixie-cron OR rule is applied when both day-of-month and day-of-week are
restricted.  Returns UTC instants; DST-nonexistent wall times are skipped.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

_FIELD_BOUNDS: tuple[tuple[int, int], ...] = (
    (0, 59),
    (0, 23),
    (1, 31),
    (1, 12),
    (0, 7),
)
_DAY_NAMES = ("minute", "hour", "dom", "month", "dow")
_SCAN_DAYS = 400

_OPEN = frozenset({"*"})


def parse_cron_expression(expression: str) -> tuple[tuple[int, ...], ...]:
    """Return per-field allowed-value tuples (validated, bounded subset)."""
    fields = str(expression).strip().split()
    if len(fields) != 5:
        raise ValueError("cron expression must have exactly 5 fields")
    allowed: list[tuple[int, ...]] = []
    for index, raw in enumerate(fields):
        low, high = _FIELD_BOUNDS[index]
        values: list[int] = []
        if raw == "*":
            values = list(range(low, high + 1))
        elif raw.startswith("*/"):
            step = int(raw[2:])
            if step < 1:
                raise ValueError(f"cron {_DAY_NAMES[index]} step must be positive")
            values = list(range(low, high + 1, step))
        elif raw.isdigit():
            number = int(raw)
            if not low <= number <= high:
                raise ValueError(f"cron {_DAY_NAMES[index]} out of range {low}..{high}")
            values = [number]
        else:
            raise ValueError(
                f"cron {_DAY_NAMES[index]} only allows integers, '*', or '*/step'"
            )
        allowed.append(tuple(values))
    return tuple(allowed)


def _day_matches(
    day_of_month: int,
    month: int,
    day_of_week: int,
    allowed: tuple[tuple[int, ...], ...],
) -> bool:
    months = allowed[3]
    if month not in months:
        return False
    dom_values, dow_values = allowed[2], allowed[4]
    dom_restricted = dom_values != _OPEN and len(dom_values) != 31
    dow_restricted = dow_values != _OPEN and len(dow_values) != 8
    if dom_restricted and dow_restricted:
        # Standard Vixie cron: dom OR dow when both are restricted.
        if day_of_month in dom_values or day_of_week in dow_values:
            return True
        # ``0`` and ``7`` are the same Sunday in cron.
        if day_of_week == 0 and 7 in dow_values:
            return True
        return False
    if dom_restricted:
        return day_of_month in dom_values
    if dow_restricted:
        if day_of_week in dow_values:
            return True
        return day_of_week == 0 and 7 in dow_values
    return True


def _time_candidates(
    allowed: tuple[tuple[int, ...], ...],
) -> list[tuple[int, int]]:
    hours = sorted(allowed[1])
    minutes = sorted(allowed[0])
    return [(hour, minute) for hour in hours for minute in minutes]


def _wall_time_exists(candidate: datetime, timezone: str) -> bool:
    """A DST spring-forward makes some wall times nonexistent; skip them.

    ``naive.replace(tzinfo=tz)`` attaches the fold-0 (pre-transition) offset;
    re-localizing the same instant through UTC reveals whether the wall time
    really exists in this zone on that date.
    """
    tz = ZoneInfo(timezone)
    aware = candidate.replace(tzinfo=tz)
    if aware.utcoffset() is None:
        return False
    local = aware.astimezone(UTC).astimezone(tz)
    return (local.hour, local.minute) == (candidate.hour, candidate.minute)


def next_cron_fire(
    expression: str, timezone: str, after: datetime
) -> datetime | None:
    """Earliest UTC instant strictly after ``after`` matching the cron.

    Returns ``None`` when nothing matches inside the bounded scan window
    (should never happen for a validated expression — but never loops
    forever).
    """
    allowed = parse_cron_expression(expression)
    timezone_name = timezone or "UTC"
    tz = ZoneInfo(timezone_name)
    after_utc = after if after.tzinfo is not None else after.replace(tzinfo=UTC)
    after_utc = after_utc.astimezone(UTC)
    after_local = after_utc.astimezone(tz)
    candidates = _time_candidates(allowed)
    today = after_local.date()
    for offset_days in range(0, _SCAN_DAYS):
        date = today + timedelta(days=offset_days)
        weekday = date.weekday()  # Monday=0..Sunday=6
        dow_number = (weekday + 1) % 7  # Sunday=0
        if not _day_matches(date.day, date.month, dow_number, allowed):
            continue
        start_index = 0
        if offset_days == 0:
            start_index = next(
                (
                    index
                    for index, (hour, minute) in enumerate(candidates)
                    if (hour, minute) > (after_local.hour, after_local.minute)
                ),
                len(candidates),
            )
        for hour, minute in candidates[start_index:]:
            naive = datetime(date.year, date.month, date.day, hour, minute)
            if not _wall_time_exists(naive, timezone_name):
                continue
            instant = naive.replace(tzinfo=tz).astimezone(UTC)
            if instant <= after_utc:
                continue
            return instant
    return None


def seconds_until_next_fire(expression: str, timezone: str, after: datetime) -> float | None:
    next_fire = next_cron_fire(expression, timezone, after)
    if next_fire is None:
        return None
    delta = next_fire - (after if after.tzinfo is not None else after.replace(tzinfo=UTC))
    return max(0.0, delta.total_seconds())


__all__ = [
    "next_cron_fire",
    "parse_cron_expression",
    "seconds_until_next_fire",
]
