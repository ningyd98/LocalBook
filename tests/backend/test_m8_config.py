"""M8 config matrix (PLAN-M8 §8.1): defaults, nested env, cron/timezone/Level2 validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from server.config import (
    HistorySettings,
    IndexSettings,
    SchedulerSettings,
    ServerSettings,
    Settings,
)


def test_m8_scheduler_defaults_are_safe_and_visible() -> None:
    scheduler = SchedulerSettings()
    assert scheduler.enabled is True
    assert scheduler.timezone == "UTC"
    assert scheduler.daily_cron == "0 23 * * *"
    assert scheduler.weekly_cron == "0 20 * * 0"
    assert scheduler.index_check_enabled is False
    assert scheduler.level2_auto_enabled is False
    assert scheduler.level2_auto_actions == []
    assert scheduler.job_timeout_seconds == 300
    assert scheduler.coalesce is True
    assert scheduler.max_instances == 1


def test_m8_history_retention_defaults_and_bounds() -> None:
    history = HistorySettings()
    assert history.retention_days == 30
    assert history.cleanup_enabled is True
    assert history.cleanup_interval_hours == 24
    assert history.max_scheduler_runs == 1000
    with pytest.raises(ValidationError):
        HistorySettings(retention_days=0)
    with pytest.raises(ValidationError):
        HistorySettings(max_scheduler_runs=99)
    HistorySettings(retention_days=1, max_scheduler_runs=100)


def test_m8_index_auto_rebuild_defaults_off() -> None:
    index = IndexSettings()
    assert index.auto_rebuild is False


def test_m8_nested_environment_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCALNOTE_SCHEDULER__ENABLED", "false")
    monkeypatch.setenv("LOCALNOTE_SCHEDULER__TIMEZONE", "Asia/Shanghai")
    monkeypatch.setenv("LOCALNOTE_SCHEDULER__DAILY_CRON", "30 7 * * *")
    monkeypatch.setenv("LOCALNOTE_SCHEDULER__LEVEL2_AUTO_ENABLED", "true")
    monkeypatch.setenv(
        "LOCALNOTE_SCHEDULER__LEVEL2_AUTO_ACTIONS", '["add_tags"]'
    )
    monkeypatch.setenv("LOCALNOTE_HISTORY__RETENTION_DAYS", "7")
    settings = Settings()
    assert settings.scheduler.enabled is False
    assert settings.scheduler.timezone == "Asia/Shanghai"
    assert settings.scheduler.daily_cron == "30 7 * * *"
    assert settings.scheduler.level2_auto_enabled is True
    assert settings.scheduler.level2_auto_actions == ["add_tags"]
    assert settings.history.retention_days == 7


def test_m8_cron_validation_rejects_bad_expressions() -> None:
    for bad in (
        "61 0 * * *",
        "0 24 * * *",
        "0 0 0 * *",
        "0 0 * 13 *",
        "0 0 * * 8",
        "0 0 * *",
        "0 0 * * * extra",
        "* * * ? *",
        "1-5 * * * *",
        "0,15 * * * *",
        "mon * * * *",
    ):
        with pytest.raises(ValidationError):
            SchedulerSettings(daily_cron=bad)
    SchedulerSettings(daily_cron="*/15 * * * *")
    SchedulerSettings(daily_cron="0 23 * * 0")


def test_m8_timezone_validation_rejects_unknown_zones() -> None:
    with pytest.raises(ValidationError):
        SchedulerSettings(timezone="Mars/Olympus")
    with pytest.raises(ValidationError):
        SchedulerSettings(timezone="")
    SchedulerSettings(timezone="Asia/Shanghai")


def test_m8_level2_whitelist_restricted_and_consistency() -> None:
    with pytest.raises(ValidationError):
        SchedulerSettings(level2_auto_actions=["patch_note"])
    with pytest.raises(ValidationError):
        SchedulerSettings(level2_auto_actions=["add_tags", "delete_note"])
    with pytest.raises(ValidationError):
        SchedulerSettings(level2_auto_enabled=True, level2_auto_actions=[])
    SchedulerSettings(level2_auto_enabled=True, level2_auto_actions=["remove_tags"])
    SchedulerSettings(level2_auto_enabled=True, level2_auto_actions=["add_tags", "remove_tags"])


def test_m8_interval_and_time_bounds() -> None:
    with pytest.raises(ValidationError):
        SchedulerSettings(index_check_interval_hours=0)
    with pytest.raises(ValidationError):
        SchedulerSettings(index_check_interval_hours=200)
    with pytest.raises(ValidationError):
        SchedulerSettings(job_timeout_seconds=0)
    with pytest.raises(ValidationError):
        SchedulerSettings(misfire_grace_seconds=86401)
    with pytest.raises(ValidationError):
        SchedulerSettings(stale_run_after_seconds=59)
    SchedulerSettings(index_check_interval_hours=1, job_timeout_seconds=1)


def test_m8_server_host_flat_alias_and_warning_trigger() -> None:
    server = ServerSettings()
    assert server.host == "127.0.0.1"
    assert "http://127.0.0.1:5173" in server.cors_origins
