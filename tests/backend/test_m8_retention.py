"""M8 History retention tests (PLAN-M8 §8.1): terminal-only deletion, keeps
active/awaiting/recovery, journal cascade, run caps, Vault never touched."""

from __future__ import annotations

from pathlib import Path

import m7support as m7

from server.history.service import HistoryService
from server.scheduler.cleanup import CleanupService


def _seed(history: HistoryService, prefix: str, days_ago: int, status: str) -> str:
    job_id = f"{prefix}-{status}-{days_ago}d"
    history.record(
        job_id=job_id,
        task_type="manual",
        status=status,
        start_time=_iso(days_ago),
        end_time=_iso(days_ago) if status not in ("awaiting_confirmation", "executing") else None,
        files_read=[],
        proposed_actions=[],
        diff=[],
        before_hash={},
        after_hash={},
    )
    return job_id


def _iso(days_ago: int) -> str:
    from datetime import UTC, datetime, timedelta

    return (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()


def _seed_run(history: HistoryService, task: str, days_ago: int, status: str, run_id: str) -> None:
    history.save_run(
        {
            "run_id": run_id,
            "task": task,
            "trigger": "manual",
            "status": status,
            "idempotency_key": f"{task}:{run_id}",
            "agent_job_id": None,
            "scheduled_for": _iso(days_ago),
            "started_at": _iso(days_ago),
            "finished_at": _iso(days_ago) if status not in ("running", "queued") else None,
            "error_code": None,
            "message": None,
            "created_at": _iso(days_ago),
        }
    )


def _seed_journal(history: HistoryService, job_id: str) -> None:
    history.append_journal(
        __import__(
            "server.history.schemas", fromlist=["JournalEntry"]
        ).JournalEntry(
            job_id=job_id,
            seq=0,
            operation="update",
            path="notes/a.md",
            before_exists=True,
            before_bytes_base64=None,
            before_hash=None,
            after_exists=True,
            after_hash=None,
            inverse={},
            state="applied",
        )
    )


def test_retention_deletes_only_terminal_old_records(tmp_path: Path) -> None:
    vault = m7.make_vault(tmp_path, {"notes/a.md": b"# A\n"})
    history = m7.make_history(tmp_path)
    old_committed = _seed(history, "job", 90, "committed")
    _seed_journal(history, old_committed)
    _seed(history, "job", 90, "rejected")
    awaiting = _seed(history, "job", 90, "awaiting_confirmation")
    recovery = _seed(history, "job", 90, "recovery_required")
    fresh_committed = _seed(history, "job", 1, "committed")

    history.save_run(
        {
            "run_id": "old-run",
            "task": "daily_organizer",
            "trigger": "manual",
            "status": "committed",
            "idempotency_key": "k1",
            "agent_job_id": old_committed,
            "scheduled_for": _iso(90),
            "started_at": _iso(90),
            "finished_at": _iso(90),
            "error_code": None,
            "message": None,
            "created_at": _iso(90),
        }
    )
    before_note = vault.read_bytes("notes/a.md")[0]

    result = history.cleanup_retention(now_iso=_iso(0), retention_days=30, max_scheduler_runs=1000)
    assert result["deleted_jobs"] == 2  # committed@90 + rejected@90
    assert history.get(old_committed) is None
    assert history.get(awaiting) is not None
    assert history.get(recovery) is not None
    assert history.get(fresh_committed) is not None
    # journal cascaded with its job; undo material gone
    assert history.journal(old_committed) == []
    # the sole run record is the newest per task -> intentionally kept
    assert result["deleted_runs"] == 0
    assert history.get_run("old-run") is not None
    # Vault byte-identical
    assert vault.read_bytes("notes/a.md")[0] == before_note


def test_retention_keeps_newest_run_per_task(tmp_path: Path) -> None:
    history = m7.make_history(tmp_path)
    for index in range(3):
        _seed_run(
            history,
            "daily_organizer",
            days_ago=90,
            status="previewed",
            run_id=f"old-run-{index}",
        )
    _seed_run(
        history,
        "daily_organizer",
        days_ago=90,
        status="previewed",
        run_id="newest-run",
    )
    # give "newest-run" the latest start time
    from datetime import timedelta

    from server.scheduler.clock import parse_iso

    run = history.get_run("newest-run")
    run["started_at"] = (parse_iso(str(run["started_at"])) + timedelta(hours=1)).isoformat()
    history.save_run(run)
    history.cleanup_retention(now_iso=_iso(0), retention_days=30, max_scheduler_runs=1000)
    page = history.page_runs(task="daily_organizer")
    assert page["total"] == 1
    assert page["items"][0]["run_id"] == "newest-run"


def test_retention_caps_scheduler_runs(tmp_path: Path) -> None:
    history = m7.make_history(tmp_path)
    for index in range(5):
        _seed_run(history, "daily_organizer", days_ago=10, status="committed", run_id=f"r{index}")
    result = history.cleanup_retention(
        now_iso=_iso(0), retention_days=30, max_scheduler_runs=2
    )
    assert result["deleted_runs"] == 3
    page = history.page_runs(task="daily_organizer")
    assert page["total"] == 2


def test_cleanup_keeps_active_and_recovery_runs(tmp_path: Path) -> None:
    history = m7.make_history(tmp_path)
    _seed_run(history, "daily_organizer", 90, "running", "active-run")
    _seed_run(history, "weekly_review", 90, "recovery_required", "recovery-run")
    result = history.cleanup_retention(now_iso=_iso(0), retention_days=30, max_scheduler_runs=1000)
    assert result["deleted_runs"] == 0
    assert history.get_run("active-run") is not None
    assert history.get_run("recovery-run") is not None


def test_cleanup_interval_guard_and_force(tmp_path: Path) -> None:
    history = m7.make_history(tmp_path)
    _seed_run(history, "daily_organizer", 90, "committed", "old")
    cleanup = CleanupService(
        history,
        retention_days=30,
        cleanup_interval_hours=24,
        max_scheduler_runs=1000,
    )
    first = cleanup.run_cleanup(now_iso=_iso(0))
    assert first["skipped"] is False
    second = cleanup.run_if_due(now_iso=_iso(0))  # same instant -> guarded
    assert second is None
    # after the interval it runs again
    from datetime import timedelta

    from server.scheduler.clock import parse_iso

    later = (parse_iso(_iso(0)) + timedelta(hours=25)).isoformat()
    third = cleanup.run_if_due(now_iso=later)
    assert third is not None and third["skipped"] is False


def test_cleanup_disabled_by_config(tmp_path: Path) -> None:
    history = m7.make_history(tmp_path)
    cleanup = CleanupService(history, cleanup_enabled=False)
    assert cleanup.run_if_due(now_iso=_iso(0)) is None
    forced = cleanup.run_if_due(force=True, now_iso=_iso(0))
    assert forced is not None


def test_deleted_history_means_undo_unavailable(tmp_path: Path) -> None:
    history = m7.make_history(tmp_path)
    job_id = _seed(history, "job", 90, "committed")
    history.cleanup_retention(now_iso=_iso(0), retention_days=30, max_scheduler_runs=1000)
    assert history.get(job_id) is None  # /history/{id}/undo will 404 job_not_found
