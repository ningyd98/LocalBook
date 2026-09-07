"""M8 backend tests (PLAN-M8 §8.1): cron math, registration, start/stop idempotency,
APScheduler adapter, asyncio fallback ticks (fake clock, no real sleeps)."""

from __future__ import annotations

from datetime import UTC, datetime

import m8support as m8
import pytest

from server.config import SchedulerSettings
from server.scheduler.apscheduler_backend import _APSCHEDULER_AVAILABLE, APSchedulerBackend
from server.scheduler.asyncio_backend import AsyncioBackend
from server.scheduler.cron import next_cron_fire, parse_cron_expression
from server.scheduler.models import JobDefinition
from server.scheduler.registry import all_task_statuses, build_definitions

_UTC = UTC


# ---------------------------------------------------------------------------
# Pure cron math
# ---------------------------------------------------------------------------


def test_parse_cron_expression_bounds() -> None:
    minute, hour, dom, month, dow = parse_cron_expression("*/15 8 1 2 0")
    assert minute == tuple(range(0, 60, 15))
    assert hour == (8,)
    assert dom == (1,)
    assert month == (2,)
    assert 0 in dow and 7 not in dow
    with pytest.raises(ValueError):
        parse_cron_expression("a b c d e")
    with pytest.raises(ValueError):
        parse_cron_expression("0 0 * *")


def test_next_cron_fire_daily_and_weekly() -> None:
    base = datetime(2026, 8, 16, 12, 0, tzinfo=_UTC)  # a Sunday
    daily = next_cron_fire("0 23 * * *", "UTC", base)
    assert daily is not None and daily.isoformat() == "2026-08-16T23:00:00+00:00"
    weekly = next_cron_fire("0 20 * * 0", "UTC", base)
    # Today is Sunday so the next Sunday slot is today 20:00.
    assert weekly is not None and weekly.isoformat() == "2026-08-16T20:00:00+00:00"
    later = next_cron_fire("0 20 * * 0", "UTC", datetime(2026, 8, 16, 21, 0, tzinfo=_UTC))
    assert later is not None and later.isoformat() == "2026-08-23T20:00:00+00:00"


def test_next_cron_fire_timezone_offset() -> None:
    base = datetime(2026, 8, 16, 12, 0, tzinfo=_UTC)
    found = next_cron_fire("0 23 * * *", "Asia/Shanghai", base)
    assert found is not None and found.isoformat() == "2026-08-16T15:00:00+00:00"


def _weekly_definition() -> JobDefinition:
    return JobDefinition(
        job_id="weekly_review",
        trigger="cron",
        expression="0 20 * * 0",  # Vixie: Sunday 20:00 (default weekly_review)
        timezone="UTC",
    )


def test_weekly_cron_fires_on_sunday_in_fallback_backend() -> None:
    """B1: the default weekly expression (Vixie dow 0 = Sunday) fires on
    Sunday in the asyncio/fallback backend, not Monday."""
    sunday_noon = datetime(2026, 8, 16, 12, 0, tzinfo=_UTC)  # a Sunday
    clock = m8.FakeClock(sunday_noon)
    backend = AsyncioBackend(clock=clock)
    fired: list[str] = []
    backend.add(_weekly_definition(), lambda: fired.append("weekly"))
    assert backend.tick(clock.now_utc()) == 0  # not due yet at Sunday noon
    clock.set(datetime(2026, 8, 16, 20, 0, tzinfo=_UTC))  # Sunday 20:00
    assert backend.tick(clock.now_utc()) == 1
    assert fired == ["weekly"]
    clock.set(datetime(2026, 8, 17, 20, 0, tzinfo=_UTC))  # Monday 20:00: no fire
    assert backend.tick(clock.now_utc()) == 0
    assert fired == ["weekly"]
    backend.stop(wait=False)


@pytest.mark.skipif(not _APSCHEDULER_AVAILABLE, reason="apscheduler not installed")
def test_apscheduler_weekly_trigger_fires_sunday_matching_fallback() -> None:
    """B1: APScheduler adapter translates the Vixie dow field so weekly fires
    on Sunday and agrees with the asyncio fallback / status computation."""
    sunday_noon = datetime(2026, 8, 16, 12, 0, tzinfo=_UTC)
    backend = APSchedulerBackend(SchedulerSettings(), clock=m8.FakeClock(sunday_noon))
    trigger = backend._trigger_for(_weekly_definition())
    aps_fire = trigger.get_next_fire_time(None, sunday_noon)
    fallback_fire = next_cron_fire("0 20 * * 0", "UTC", sunday_noon)
    assert aps_fire is not None and fallback_fire is not None
    assert aps_fire.weekday() == 6  # Sunday in Python weekday ordering
    assert aps_fire == fallback_fire


@pytest.mark.skipif(not _APSCHEDULER_AVAILABLE, reason="apscheduler not installed")
@pytest.mark.parametrize(
    "expression",
    [
        "0 20 * * 0",  # Sunday via 0
        "0 20 * * 7",  # Sunday via 7
        "0 9 * * 1",  # Monday
        "0 9 * * 6",  # Saturday
        "0 20 * * */2",  # Vixie step over 0..7 -> Sun,Tue,Thu,Sat
        "0 20 * * *",  # every day unchanged
    ],
)
def test_apscheduler_dow_mapping_matches_fallback_for_all_forms(expression: str) -> None:
    sunday_noon = datetime(2026, 8, 16, 12, 0, tzinfo=_UTC)
    definition = JobDefinition(
        job_id="weekly_review",
        trigger="cron",
        expression=expression,
        timezone="UTC",
    )
    backend = APSchedulerBackend(SchedulerSettings(), clock=m8.FakeClock(sunday_noon))
    trigger = backend._trigger_for(definition)
    aps_fire = trigger.get_next_fire_time(None, sunday_noon)
    fallback_fire = next_cron_fire(expression, "UTC", sunday_noon)
    assert aps_fire is not None and fallback_fire is not None
    assert aps_fire == fallback_fire, (expression, aps_fire, fallback_fire)


def test_next_cron_fire_skips_nonexistent_dst_wall_time() -> None:
    base = datetime(2026, 3, 8, 5, 0, tzinfo=_UTC)  # US spring-forward day 05:00Z
    found = next_cron_fire("30 2 * * *", "America/New_York", base)
    # 02:30 local does not exist on 2026-03-08 -> next valid is 03-09 02:30 EDT.
    assert found is not None and found.isoformat() == "2026-03-09T06:30:00+00:00"


def test_next_cron_fire_strict_subset_no_lists_or_names() -> None:
    with pytest.raises(ValueError):
        next_cron_fire("0 0 * * mon", "UTC", datetime(2026, 1, 1, tzinfo=_UTC))
    with pytest.raises(ValueError):
        next_cron_fire("0 12 1,15 * *", "UTC", datetime(2026, 1, 1, tzinfo=_UTC))


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_registry_definitions_static_and_index_disabled_by_default() -> None:
    settings = SchedulerSettings()
    definitions = build_definitions(settings)
    ids = [definition.job_id for definition in definitions]
    assert ids == ["daily_organizer", "weekly_review"]
    assert definitions[0].trigger == "cron"
    assert definitions[0].expression == "0 23 * * *"
    settings_index = SchedulerSettings(index_check_enabled=True, index_check_interval_hours=6)
    definitions_index = build_definitions(settings_index)
    assert [definition.job_id for definition in definitions_index] == [
        "daily_organizer",
        "weekly_review",
        "index_consistency",
    ]
    index_job = definitions_index[-1]
    assert index_job.trigger == "interval" and index_job.expression == "6"


def test_registry_all_task_statuses_marks_index_disabled() -> None:
    rows = all_task_statuses(SchedulerSettings())
    by_id = {str(row["id"]): row for row in rows}
    assert by_id["daily_organizer"]["enabled"] is True
    assert by_id["weekly_review"]["enabled"] is True
    assert by_id["index_consistency"]["enabled"] is False
    assert by_id["index_consistency"]["trigger"] == "interval"


# ---------------------------------------------------------------------------
# Asyncio fallback (fake clock + explicit ticks)
# ---------------------------------------------------------------------------


def _daily_definition() -> JobDefinition:
    return JobDefinition(
        job_id="daily_organizer",
        trigger="cron",
        expression="0 23 * * *",
        timezone="UTC",
    )


def test_asyncio_backend_tick_fires_daily_once() -> None:
    clock = m8.FakeClock(datetime(2026, 8, 16, 12, 0, tzinfo=_UTC))
    backend = AsyncioBackend(clock=clock)
    fired: list[str] = []
    backend.add(_daily_definition(), lambda: fired.append("daily"))
    assert backend.tick(clock.now_utc()) == 0  # not due yet
    clock.set(datetime(2026, 8, 16, 23, 0, tzinfo=_UTC))
    assert backend.tick(clock.now_utc()) == 1
    assert fired == ["daily"]
    # same slot fired once; no burst
    assert backend.tick(clock.now_utc()) == 0
    assert fired == ["daily"]
    clock.set(datetime(2026, 8, 17, 23, 0, tzinfo=_UTC))
    assert backend.tick(clock.now_utc()) == 1
    assert fired == ["daily", "daily"]


def test_asyncio_backend_interval_and_status() -> None:
    clock = m8.FakeClock(datetime(2026, 8, 16, 12, 0, tzinfo=_UTC))
    backend = AsyncioBackend(clock=clock)
    fired: list[str] = []
    definition = JobDefinition(
        job_id="index_consistency", trigger="interval", expression="1", timezone="UTC"
    )
    backend.add(definition, lambda: fired.append("index"))
    clock.advance(3600)
    assert backend.tick(clock.now_utc()) == 1
    status = backend.status()
    assert status["backend"] == "asyncio"
    assert status["jobs"][0]["job_id"] == "index_consistency"
    assert status["jobs"][0]["next_run_at"] is not None
    assert status["jobs"][0]["last_run_at"] is not None
    backend.stop(wait=False)


def test_asyncio_backend_duplicate_add_rejected_and_remove() -> None:
    backend = AsyncioBackend(clock=m8.FakeClock())
    backend.add(_daily_definition(), lambda: None)
    with pytest.raises(ValueError):
        backend.add(_daily_definition(), lambda: None)
    backend.remove("daily_organizer")
    assert backend.registered_ids() == []


# ---------------------------------------------------------------------------
# APScheduler adapter
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _APSCHEDULER_AVAILABLE, reason="apscheduler not installed")
def test_apscheduler_backend_start_stop_idempotent() -> None:
    backend = APSchedulerBackend(SchedulerSettings(), clock=m8.FakeClock())
    fired: list[str] = []
    backend.add(_daily_definition(), lambda: fired.append("daily"))
    backend.start()
    assert backend.running is True
    assert backend.registered_ids() == ["daily_organizer"]
    backend.start()  # second start is a no-op
    status = backend.status()
    assert status["backend"] == "apscheduler"
    assert status["running"] is True
    jobs = {job["job_id"]: job for job in status["jobs"]}
    assert "daily_organizer" in jobs
    backend.stop()
    backend.stop()  # idempotent
    assert backend.running is False


@pytest.mark.skipif(not _APSCHEDULER_AVAILABLE, reason="apscheduler not installed")
def test_apscheduler_backend_run_now_fires_callback() -> None:
    import threading

    backend = APSchedulerBackend(SchedulerSettings(), clock=m8.FakeClock())
    event = threading.Event()
    fired: list[str] = []

    def _callback() -> None:
        fired.append("daily")
        event.set()

    backend.add(_daily_definition(), _callback)
    backend.start()
    handle = backend.run_now("daily_organizer")
    assert handle is not None
    assert event.wait(timeout=5.0)
    backend.stop()
    assert fired == ["daily"]


def test_apscheduler_backend_degraded_when_missing() -> None:
    if _APSCHEDULER_AVAILABLE:
        pytest.skip("apscheduler is installed in this environment")
    backend = APSchedulerBackend(SchedulerSettings())
    assert backend.available is False
    backend.start()  # no-op, never crashes
    assert backend.running is False
