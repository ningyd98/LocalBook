"""M8 crash-recovery tests (PLAN-M8 §8.1): startup scan flags only,
no auto write/rollback, explicit diagnose + hash-guarded rollback."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import m7support as m7
import m8support as m8

from server.actions.diff import sha256
from server.history.schemas import JournalEntry
from server.history.service import HistoryService
from server.recovery.scanner import RecoveryScanner
from server.recovery.schemas import JobStatus
from server.recovery.service import RecoveryService
from server.scheduler.models import SchedulerRunStatus


def _crashed_state(tmp_path: Path):
    """Vault + history with an interrupted executing job and a running run."""
    vault = m7.make_vault(tmp_path, {"notes/a.md": b"# A\n"})
    history = m7.make_history(tmp_path)
    now = "2026-08-16T10:00:00+00:00"
    history.record(
        job_id="job-crashed",
        task_type="daily_organizer",
        status=JobStatus.EXECUTING.value,
        start_time=now,
        model="mock-qwen",
        prompt_version=None,
        files_read=["notes/a.md"],
        proposed_actions=[],
        diff=[],
        before_hash={},
        after_hash={},
    )
    history.save_run(
        {
            "run_id": "run-crashed",
            "task": "daily_organizer",
            "trigger": "scheduled",
            "status": SchedulerRunStatus.RUNNING.value,
            "idempotency_key": "daily_organizer:slot",
            "agent_job_id": "job-crashed",
            "scheduled_for": now,
            "started_at": now,
            "finished_at": None,
            "error_code": None,
            "message": None,
            "created_at": now,
        }
    )
    return vault, history


def test_startup_scan_returns_diagnosis_without_side_effects(tmp_path: Path) -> None:
    vault, history = _crashed_state(tmp_path)
    before = vault.read_bytes("notes/a.md")[0]
    scan = RecoveryScanner(history).scan()
    assert scan["scanned"] is True
    assert len(scan["flagged_jobs"]) == 1
    assert scan["flagged_jobs"][0]["job_id"] == "job-crashed"
    assert scan["flagged_jobs"][0]["reason"] == "process_interrupted"
    assert len(scan["flagged_runs"]) == 1
    assert scan["total_recovery_required"] == 2
    # no auto-write, no rollback, no model call, no status mutation
    assert vault.read_bytes("notes/a.md")[0] == before
    assert history.get("job-crashed").status == JobStatus.EXECUTING.value  # type: ignore[union-attr]
    assert history.get_run("run-crashed")["status"] == SchedulerRunStatus.RUNNING.value


def test_mark_recovery_required_flags_derived_rows_only(tmp_path: Path) -> None:
    vault, history = _crashed_state(tmp_path)
    result = RecoveryScanner(history).mark_recovery_required()
    assert result["marked_jobs"] == 1
    assert result["marked_runs"] == 1
    job = history.get("job-crashed")
    assert job is not None
    assert job.status == JobStatus.RECOVERY_REQUIRED.value
    assert job.error is not None and job.error["code"] == "process_interrupted"
    run = history.get_run("run-crashed")
    assert run["status"] == SchedulerRunStatus.RECOVERY_REQUIRED.value
    # vault file untouched
    assert b"# A\n" == vault.read_bytes("notes/a.md")[0]


def test_recovery_required_count_and_status(tmp_path: Path) -> None:
    _vault, history = _crashed_state(tmp_path)
    RecoveryScanner(history).mark_recovery_required()
    # flagged run + flagged job dedupe to one distinct recovery record
    assert history.recovery_required_count() == 1


def test_scan_without_repository_is_safe(tmp_path: Path) -> None:
    scanner = RecoveryScanner(None)
    assert scanner.scan()["scanned"] is False
    assert scanner.mark_recovery_required()["marked_jobs"] == 0


# ---------------------------------------------------------------------------
# Explicit recovery: diagnose / rollback_if_safe (hash guarded)
# ---------------------------------------------------------------------------


def _history_with_executed_update(root: Path, history: HistoryService) -> None:
    """Journal row + vault bytes consistent with 'step applied then crash'."""
    after = b"# A\n\nchanged by agent\n"
    vault = m7.make_vault(root, {"notes/a.md": after})
    history.record(
        job_id="job-half",
        task_type="daily_organizer",
        status=JobStatus.EXECUTING.value,
        start_time="2026-08-16T10:00:00+00:00",
        files_read=["notes/a.md"],
        proposed_actions=[],
        diff=[],
        before_hash={"notes/a.md": None},
        after_hash={"notes/a.md": sha256(after)},
    )
    before = b"# A\n"
    history.append_journal(
        JournalEntry(
            job_id="job-half",
            seq=0,
            operation="update",
            path="notes/a.md",
            before_exists=True,
            before_bytes_base64=_b64(before),
            before_hash=sha256(before),
            after_exists=True,
            after_hash=sha256(after),
            inverse={},
            state="pending",
        )
    )
    return vault, history


def _b64(data: bytes) -> str:
    import base64

    return base64.b64encode(data).decode("ascii")


def test_diagnose_reads_journal_and_current_hash(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    history = m7.make_history(tmp_path)
    vault, history = _history_with_executed_update(root, history)
    recovery = RecoveryService(vault, history)
    diagnosis = recovery.diagnose("job-half")
    assert diagnosis.status == JobStatus.EXECUTING.value
    assert len(diagnosis.journal_steps) == 1
    step = diagnosis.journal_steps[0]
    assert step.operation == "update"
    assert step.current_matches_after is True
    assert step.state == "pending"


def test_rollback_if_safe_restores_before_bytes(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    history = m7.make_history(tmp_path)
    vault, history = _history_with_executed_update(root, history)
    recovery = RecoveryService(vault, history)
    result = recovery.rollback_if_safe("job-half")
    assert result.status == JobStatus.ROLLED_BACK
    assert result.restored == ["notes/a.md"]
    assert vault.read_bytes("notes/a.md")[0] == b"# A\n"
    job = history.get("job-half")
    assert job is not None and job.status == JobStatus.ROLLED_BACK.value


def test_rollback_if_safe_conflicts_on_external_change(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    history = m7.make_history(tmp_path)
    vault, history = _history_with_executed_update(root, history)
    # external edit after the crash: current hash no longer matches after_hash
    external = b"# A\n\nsomeone else\n"
    changed = b"# A\n\nchanged by agent\n"
    vault.write_bytes("notes/a.md", external, expected_sha256=sha256(changed))
    recovery = RecoveryService(vault, history)
    result = recovery.rollback_if_safe("job-half")
    assert result.status == JobStatus.CONFLICT
    assert result.conflict_paths == ["notes/a.md"]
    # external content must not be overwritten
    assert vault.read_bytes("notes/a.md")[0] == b"# A\n\nsomeone else\n"


def test_rollback_if_safe_job_without_journal_marks_nothing_written(
    tmp_path: Path,
) -> None:
    vault = m7.make_vault(tmp_path / "vault", {"notes/a.md": b"# A\n"})
    history = m7.make_history(tmp_path)
    history.record(
        job_id="job-early",
        task_type="daily_organizer",
        status=JobStatus.EXECUTING.value,
        start_time="2026-08-16T10:00:00+00:00",
        files_read=["notes/a.md"],
        proposed_actions=[],
        diff=[],
        before_hash={},
        after_hash={},
    )
    recovery = RecoveryService(vault, history)
    result = recovery.rollback_if_safe("job-early")
    assert result.status == JobStatus.ROLLED_BACK
    assert vault.read_bytes("notes/a.md")[0] == b"# A\n"


def test_rollback_if_safe_refuses_committed_jobs(tmp_path: Path) -> None:
    vault = m7.make_vault(tmp_path / "vault", {"notes/a.md": b"# A\n"})
    history = m7.make_history(tmp_path)
    history.record(
        job_id="job-done",
        task_type="manual",
        status=JobStatus.COMMITTED.value,
        start_time="2026-08-16T10:00:00+00:00",
        end_time="2026-08-16T10:01:00+00:00",
        files_read=["notes/a.md"],
        proposed_actions=[],
        diff=[],
        before_hash={},
        after_hash={},
    )
    recovery = RecoveryService(vault, history)
    result = recovery.rollback_if_safe("job-done")
    assert result.status == JobStatus.UNDO_UNAVAILABLE
    assert "not in an uncertain state" in (result.error or "")


def test_scanner_uses_injectable_clock(tmp_path: Path) -> None:
    """S4: RecoveryScanner accepts a Clock, so scan/mark timestamps are
    deterministic with the same fake clock the scheduler main path uses."""
    pinned = m8.FakeClock(datetime(2026, 8, 16, 7, 30, 0, tzinfo=UTC))
    scanner = RecoveryScanner(None, clock=pinned)
    result = scanner.scan()
    assert result["scanned_at"] == "2026-08-16T07:30:00+00:00"
    # mark path writes the injected instant into finished_at
    _vault, history = _crashed_state(tmp_path)
    marked = RecoveryScanner(history, clock=pinned).mark_recovery_required()
    assert marked["marked_runs"] == 1
    run = history.get_run("run-crashed")
    assert run is not None and run["finished_at"] == "2026-08-16T07:30:00+00:00"
    # the service facade forwards its own clock (defaults still work)
    scanner_default = RecoveryScanner(None)
    assert scanner_default.scan()["scanned"] is False
