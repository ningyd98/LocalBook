"""M8 scheduler ↔ M7 integration (PLAN-M8 §8.1): same pipeline for scheduled
and manual triggers, run records, Level2 auto, duplicates, timeouts."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import m7support as m7
import m8support as m8
import pytest

from server.actions.schemas import ActionType
from server.agents.schemas import AcceptJobRequest
from server.config import SchedulerSettings
from server.policies.engine import PolicyEngine
from server.scheduler.errors import JobInProgress, JobTimeout
from server.scheduler.models import SchedulerRunStatus
from server.scheduler.service import SchedulerService

ADD_TAGS = (
    '{"actions":[{"action":"add_tags","file":"notes/a.md","tags":["inbox"],'
    '"reason":"m8 integration"}],"task_type":"daily_organizer"}'
)


def _build(
    tmp_path: Path,
    *,
    scheduler: SchedulerSettings | None = None,
    adapter: Any = None,
    policy: PolicyEngine | None = None,
) -> tuple[SchedulerService, m8.FakeBackend, m8.FakeClock, Any, Any]:
    """Service wired to M7 with a recording fake backend + fake clock."""
    vault = m7.make_vault(
        tmp_path,
        {"notes/a.md": b"---\ntags:\n  - work\n---\n# A\n\nbody\n"},
    )
    history = m7.make_history(tmp_path)
    scheduler_settings = scheduler or SchedulerSettings()
    agent = m7.make_service(
        vault,
        history=history,
        policy=policy,
        adapter=adapter,
        default_model="mock-qwen",
    )
    clock = m8.FakeClock()
    backend = m8.FakeBackend()
    service = SchedulerService(
        settings=scheduler_settings,
        history=history,
        agent_service=agent,
        vault=vault,
        index_service=None,
        clock=clock,
        backend_factory=lambda: backend,
    )
    return service, backend, clock, vault, history


def _level2_scheduler_settings() -> SchedulerSettings:
    return SchedulerSettings(
        enabled=True,
        level2_auto_enabled=True,
        level2_auto_actions=["add_tags"],
        stale_run_after_seconds=60,
        job_timeout_seconds=30,
    )


def _level2_policy() -> PolicyEngine:
    return PolicyEngine(
        level2_auto_actions={ActionType.ADD_TAGS},
        level2_max_files=1,
        max_level2_modified_chars=2000,
    )


async def _wait_for(predicate, timeout: float = 5.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition was not reached in time")


# ---------------------------------------------------------------------------
# Scheduled fire → Level 1 preview (awaiting_confirmation, no write)
# ---------------------------------------------------------------------------


async def test_scheduled_daily_produces_previewed_run_no_write(tmp_path: Path) -> None:
    service, backend, _clock, vault, history = _build(
        tmp_path, adapter=m8.FakeAdapter(ADD_TAGS)
    )
    try:
        service.start()
        backend.fire("daily_organizer")  # scheduled trigger entry
        await _wait_for(
            lambda: (service.list_runs(task="daily_organizer")["total"] or 0) >= 1
        )

        def _settled() -> bool:
            page = service.list_runs(task="daily_organizer")
            return bool(page["items"]) and page["items"][0]["status"] not in (
                "queued",
                "running",
            )

        await _wait_for(_settled)
        run = service.list_runs(task="daily_organizer")["items"][0]
        assert run["status"] == "previewed"
        assert run["trigger"] == "scheduled"
        assert run["task"] == "daily_organizer"
        assert run["agent_job_id"]
        record = history.get(str(run["agent_job_id"]))
        assert record is not None and record.status == "awaiting_confirmation"
        assert b"inbox" not in vault.read_bytes("notes/a.md")[0]
    finally:
        service.stop()


# ---------------------------------------------------------------------------
# Manual trigger shares the exact pipeline
# ---------------------------------------------------------------------------


async def test_manual_trigger_shares_pipeline_and_records(tmp_path: Path) -> None:
    service, backend, _clock, vault, history = _build(
        tmp_path, adapter=m8.FakeAdapter(ADD_TAGS)
    )
    try:
        service.start()
        response = await service.arun_task("daily_organizer", {"confirm": True})
        assert response["status"] == "previewed"
        assert response["trigger"] == "manual"
        assert response["agent_job_id"]
        assert response["policy"]["decision"] == "confirm"
        # user accepts through the existing M7 endpoint; run record stays previewed
        committed = service.agent_service.accept(  # type: ignore[union-attr]
            str(response["agent_job_id"]),
            AcceptJobRequest(confirm=True, action_ids=[]),
        )
        assert committed["status"] == "committed"
        assert b"inbox" in vault.read_bytes("notes/a.md")[0]
        record = history.get(str(response["agent_job_id"]))
        assert record is not None and record.status == "committed"
    finally:
        service.stop()


async def test_manual_daily_ai_unavailable_fails_safely(tmp_path: Path) -> None:
    service, _backend, _clock, vault, _history = _build(tmp_path, adapter=None)
    try:
        service.start()
        response = await service.arun_task("daily_organizer", {"confirm": True})
        assert response["status"] == "failed"
        assert response["error_code"] in ("ai_unavailable", "scheduler_unavailable")
    finally:
        service.stop()


# ---------------------------------------------------------------------------
# Level 2 tag-only auto through the service
# ---------------------------------------------------------------------------


async def test_level2_auto_commits_run_and_history(tmp_path: Path) -> None:
    service, _backend, _clock, vault, history = _build(
        tmp_path,
        scheduler=_level2_scheduler_settings(),
        adapter=m8.FakeAdapter(ADD_TAGS),
        policy=_level2_policy(),
    )
    try:
        service.start()
        response = await service.arun_task(
            "daily_organizer", {"confirm": True, "auto_level2": True}
        )
        assert response["status"] == "committed"
        assert b"inbox" in vault.read_bytes("notes/a.md")[0]
        record = history.get(str(response["agent_job_id"]))
        assert record is not None
        assert record.status == "committed"
        assert record.permission_level == 2
        assert len(history.journal(str(response["agent_job_id"]))) >= 1
    finally:
        service.stop()


async def test_level2_client_cannot_force_auto_without_server_switch(
    tmp_path: Path,
) -> None:
    service, _backend, _clock, vault, _history = _build(
        tmp_path,
        scheduler=SchedulerSettings(enabled=True),
        adapter=m8.FakeAdapter(ADD_TAGS),
        policy=_level2_policy(),
    )
    try:
        service.start()
        response = await service.arun_task(
            "daily_organizer", {"confirm": True, "auto_level2": True}
        )
        assert response["status"] == "previewed"  # server switch off -> Level 1
        assert b"inbox" not in vault.read_bytes("notes/a.md")[0]
    finally:
        service.stop()


# ---------------------------------------------------------------------------
# Duplicates / gating / timeouts
# ---------------------------------------------------------------------------


def test_active_run_blocks_new_manual_run(tmp_path: Path) -> None:
    service, _backend, _clock, _vault, _history = _build(tmp_path, adapter=None)
    service.start()
    try:
        first = service._begin_run(task="daily_organizer", trigger="manual")
        assert first
        with pytest.raises(JobInProgress):
            service._begin_run(task="daily_organizer", trigger="manual")
    finally:
        service.stop()


async def test_scheduled_fire_during_active_run_is_skipped(tmp_path: Path) -> None:
    import asyncio as aio

    service, backend, _clock, _vault, _history = _build(tmp_path, adapter=None)

    class SlowRunner:
        async def arun(self, task, *, scope=None, request_auto_level2=None):
            await aio.sleep(5.0)
            from server.scheduler.runner import RunOutcome

            return RunOutcome(status=SchedulerRunStatus.PREVIEWED)

    service.runner = SlowRunner()  # type: ignore[assignment]
    service.start()
    try:
        manual_task = aio.create_task(
            service.arun_task("daily_organizer", {"confirm": True})
        )
        await _wait_for(lambda: service.list_runs(task="daily_organizer")["total"] == 1)
        # scheduled fire arrives while the manual run is still active
        backend.fire("daily_organizer")

        def _skip_seen() -> bool:
            page = service.list_runs(task="daily_organizer")
            return page["total"] == 2 and page["items"][1]["status"] in (
                "skipped_duplicate",
                "running",
            )

        await _wait_for(_skip_seen)
        manual_task.cancel()
        try:
            await manual_task
        except (aio.CancelledError, JobTimeout):
            pass
    finally:
        service.stop()
    page = service.list_runs(task="daily_organizer")
    assert page["total"] == 2
    # the duplicate marker never ran the business chain
    skipped = [item for item in page["items"] if item["status"] == "skipped_duplicate"]
    assert skipped and "active" in (skipped[0]["message"] or "")


async def test_timeout_marks_run_timed_out(tmp_path: Path) -> None:
    import asyncio as aio

    service, _backend, _clock, _vault, _history = _build(tmp_path, adapter=None)

    class SlowRunner:
        async def arun(self, task, *, scope=None, request_auto_level2=None):
            await aio.sleep(5.0)
            from server.scheduler.runner import RunOutcome

            return RunOutcome(status=SchedulerRunStatus.PREVIEWED)

    service.runner = SlowRunner()  # type: ignore[assignment]
    service.settings.job_timeout_seconds = 0.1
    service.start()
    try:
        with pytest.raises(JobTimeout):
            await service.arun_task("daily_organizer", {"confirm": True})
    finally:
        service.stop()
    run = service.list_runs(task="daily_organizer")["items"][0]
    # Either the explicit timeout mark or the follow-up recovery flag (the
    # underlying slow job finished after the timeout mark) must be recorded.
    assert run["status"] in ("timed_out", "recovery_required")


# ---------------------------------------------------------------------------
# Status / stop semantics
# ---------------------------------------------------------------------------


def test_status_reports_enabled_running_jobs_and_retention(tmp_path: Path) -> None:
    service, backend, _clock, _vault, _history = _build(tmp_path, adapter=None)
    service.start()
    try:
        status = service.status()
        assert status["enabled"] is True
        assert status["running"] is True
        assert status["backend"] == "fake"
        assert status["network_exposure_warning"] is False
        ids = [job["id"] for job in status["jobs"]]
        assert ids == ["daily_organizer", "weekly_review", "index_consistency"]
        daily = status["jobs"][0]
        assert daily["enabled"] is True and daily["next_run_at"] is not None
        index_job = status["jobs"][2]
        assert index_job["enabled"] is False and index_job["next_run_at"] is None
        assert status["recovery_required"] == 0
    finally:
        service.stop()
    stopped_status = service.status()
    assert stopped_status["running"] is False
    assert stopped_status["enabled"] is True


def test_network_warning_flag_when_host_non_loopback() -> None:
    from server.config import ServerSettings

    service = SchedulerService(
        settings=SchedulerSettings(enabled=False),
        server=ServerSettings(host="0.0.0.0", port=3780),
        history=None,
    )
    assert service.network_warning() is True
    loopback = SchedulerService(
        settings=SchedulerSettings(enabled=False),
        server=ServerSettings(host="127.0.0.1", port=3780),
        history=None,
    )
    assert loopback.network_warning() is False


# ---------------------------------------------------------------------------
# Fix-round gates: B1-related host parity, I1 retry_preview, I2 LAN scope,
# S2 confirm semantics, S3 safe unknown-exception message
# ---------------------------------------------------------------------------


def test_network_warning_true_for_any_non_loopback_host() -> None:
    from server.config import ServerSettings

    for host in ("0.0.0.0", "::", "192.168.1.5", "10.0.0.7", "localnote.lan"):
        service = SchedulerService(
            settings=SchedulerSettings(enabled=False),
            server=ServerSettings(host=host, port=3780),
            history=None,
        )
        assert service.network_warning() is True, host
    for host in ("127.0.0.1", "127.0.0.2", "localhost", "::1", ""):
        service = SchedulerService(
            settings=SchedulerSettings(enabled=False),
            server=ServerSettings(host=host, port=3780),
            history=None,
        )
        assert service.network_warning() is False, host


def test_network_advice_when_cors_not_explicit() -> None:
    from server.config import ServerSettings

    def _service(host: str, cors: list[str] | None) -> SchedulerService:
        return SchedulerService(
            settings=SchedulerSettings(enabled=False),
            server=ServerSettings(host=host, port=3780, cors_origins=cors or []),
            history=None,
        )

    # exposed host + loopback-only default whitelist -> fixed advice text
    advised = _service("0.0.0.0", ["http://127.0.0.1:5173", "http://localhost:5173"])
    assert advised.network_warning() is True
    assert advised.network_advice() is not None
    assert "LOCALNOTE_SERVER__CORS_ORIGINS" in advised.network_advice() or ""
    # exposed host + empty CORS -> advice
    assert _service("192.168.1.5", []).network_advice() is not None
    # exposed host + "*" -> advice
    assert _service("0.0.0.0", ["*"]).network_advice() is not None
    # exposed host + explicit non-loopback origin -> no advice
    explicit = _service("0.0.0.0", ["http://192.168.1.9:5173"])
    assert explicit.network_warning() is True
    assert explicit.network_advice() is None
    # loopback host never advises even with empty CORS
    loopback = _service("127.0.0.1", [])
    assert loopback.network_warning() is False
    assert loopback.network_advice() is None


def test_retry_preview_bypasses_recovery_gate_and_keeps_old_mark(tmp_path: Path) -> None:
    """I1: a recovery_required run -> recovery_action(retry_preview) creates a
    fresh run; the old row keeps its recovery_required marking."""
    service, backend, clock, _vault, history = _build(tmp_path, adapter=None)
    old_run_id = "recovery-run-1"
    history.save_run(
        {
            "run_id": old_run_id,
            "task": "daily_organizer",
            "trigger": "scheduled",
            "status": SchedulerRunStatus.RECOVERY_REQUIRED.value,
            "idempotency_key": "daily_organizer:old-slot",
            "agent_job_id": None,
            "scheduled_for": clock.now_iso(),
            "started_at": clock.now_iso(),
            "finished_at": clock.now_iso(),
            "error_code": "process_interrupted",
            "message": "process interrupted; explicit diagnosis required",
            "created_at": clock.now_iso(),
        }
    )
    try:
        service.start()
        response = service.recovery_action(old_run_id, "retry_preview")
        assert response["status"] == "failed"  # adapter=None -> safe failure
        assert response["run_id"] != old_run_id
        old = history.get_run(old_run_id)
        assert old is not None
        assert old["status"] == SchedulerRunStatus.RECOVERY_REQUIRED.value
        assert old["message"] == "process interrupted; explicit diagnosis required"
        page = service.list_runs(task="daily_organizer")
        assert page["total"] == 2
        new_run = next(
            item for item in page["items"] if item["run_id"] == response["run_id"]
        )
        # S1: idempotency key is the deterministic task:scheduled_for label,
        # never embedding the run id.
        assert new_run["idempotency_key"] == (
            f"daily_organizer:{new_run['scheduled_for']}"
        )
        assert response["run_id"] not in (new_run["idempotency_key"] or "")
    finally:
        service.stop()


async def test_level2_manual_requires_confirm_true(tmp_path: Path) -> None:
    """S2: confirm participates — an unconfirmed manual request only previews
    even when the server Level-2 switch is enabled."""
    service, _backend, _clock, vault, _history = _build(
        tmp_path,
        scheduler=_level2_scheduler_settings(),
        adapter=m8.FakeAdapter(ADD_TAGS),
        policy=_level2_policy(),
    )
    try:
        service.start()
        preview = await service.arun_task(
            "daily_organizer", {"confirm": False, "auto_level2": True}
        )
        assert preview["status"] == "previewed"
        assert b"inbox" not in vault.read_bytes("notes/a.md")[0]
        # confirm=true + auto_level2 -> the configured Level-2 chain may commit
        committed = await service.arun_task(
            "daily_organizer", {"confirm": True, "auto_level2": True}
        )
        assert committed["status"] == "committed"
        assert b"inbox" in vault.read_bytes("notes/a.md")[0]
    finally:
        service.stop()


async def test_pipeline_unknown_exception_uses_fixed_safe_message(
    tmp_path: Path,
) -> None:
    """S3: unknown exceptions never leak their str() into run.message."""
    import asyncio as aio

    service, _backend, _clock, _vault, _history = _build(tmp_path, adapter=None)

    class BoomRunner:
        async def arun(self, task, *, scope=None, request_auto_level2=None):
            await aio.sleep(0)
            raise RuntimeError("raw secret path /Users/me/notes leak-xyz")

    service.runner = BoomRunner()  # type: ignore[assignment]
    service.start()
    try:
        response = await service.arun_task("daily_organizer", {"confirm": True})
    finally:
        service.stop()
    assert response["status"] == "failed"
    assert response["error_code"] == "scheduler_unavailable"
    message = response["message"] or ""
    assert "unexpected scheduler error" in message
    assert "leak-xyz" not in message and "/Users/me" not in message
