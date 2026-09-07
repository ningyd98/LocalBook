"""M8 JobRunner tests (PLAN-M8 §8.1): Level1 preview, Level2 whitelist auto,
policy deny stays deny, one model call, no writes outside the M7 chain."""

from __future__ import annotations

import asyncio
from pathlib import Path

import m7support as m7
import m8support as m8

from server.actions.schemas import ActionType
from server.policies.engine import PolicyEngine
from server.scheduler.models import SchedulerRunStatus
from server.scheduler.runner import JobRunner

ADD_TAGS_JSON = (
    '{"actions":[{"action":"add_tags","file":"notes/a.md","tags":["inbox"],'
    '"reason":"m8 test"}],"task_type":"daily_organizer"}'
)
REMOVE_MISSING_TAG_JSON = (
    '{"actions":[{"action":"remove_tags","file":"notes/a.md","tags":["nope"],'
    '"reason":"m8 test"}],"task_type":"daily_organizer"}'
)
WEEKLY_CREATE_JSON = (
    '{"actions":[{"action":"create_note","file":"notes/weekly.md",'
    '"content_base64":"IyBXZWVrbHkK","reason":"m8 test"}],'
    '"task_type":"weekly_review"}'
)
GOOD_ACTIONS = ADD_TAGS_JSON


def _vault_and_service(tmp_path: Path, *, adapter, policy=None, history=None):
    vault = m7.make_vault(
        tmp_path,
        {"notes/a.md": b"---\ntags:\n  - work\n---\n# A\n\nbody\n"},
    )
    history = history or m7.make_history(tmp_path)
    service = m7.make_service(
        vault,
        history=history,
        policy=policy,
        adapter=adapter,
        default_model="mock-qwen",
    )
    return vault, service


def _runner(service, *, level2_enabled=False, level2_actions=()) -> JobRunner:
    return JobRunner(
        service,
        level2_auto_enabled=level2_enabled,
        level2_auto_actions=set(level2_actions),
    )


# ---------------------------------------------------------------------------
# Level 1 preview (default)
# ---------------------------------------------------------------------------


def test_level1_preview_never_writes_and_reports_awaiting(tmp_path: Path) -> None:
    adapter = m8.FakeAdapter(GOOD_ACTIONS)
    vault, service = _vault_and_service(tmp_path, adapter=adapter)
    runner = _runner(service)
    outcome = asyncio.run(runner.arun("daily_organizer", request_auto_level2=None))
    assert outcome.status == SchedulerRunStatus.PREVIEWED
    assert outcome.policy is not None
    assert outcome.policy["decision"] == "confirm"
    record = service.history.get(outcome.agent_job_id)  # type: ignore[attr-defined]
    assert record is not None and record.status == "awaiting_confirmation"
    assert record.permission_level == 1
    assert adapter.chat_calls == 1
    # preview must not have written the tag
    assert b"inbox" not in vault.read_bytes("notes/a.md")[0]


def test_scheduled_default_is_level1_even_when_level2_config_off(tmp_path: Path) -> None:
    adapter = m8.FakeAdapter(GOOD_ACTIONS)
    _vault, service = _vault_and_service(tmp_path, adapter=adapter)
    runner = _runner(service, level2_enabled=False, level2_actions={ActionType.ADD_TAGS})
    outcome = asyncio.run(runner.arun("daily_organizer", request_auto_level2=None))
    assert outcome.status == SchedulerRunStatus.PREVIEWED
    record = service.history.get(outcome.agent_job_id)  # type: ignore[attr-defined]
    assert record is not None and record.permission_level == 1


def test_client_auto_level2_cannot_open_server_switch(tmp_path: Path) -> None:
    adapter = m8.FakeAdapter(GOOD_ACTIONS)
    _vault, service = _vault_and_service(tmp_path, adapter=adapter)
    runner = _runner(service, level2_enabled=False)
    outcome = asyncio.run(runner.arun("daily_organizer", request_auto_level2=True))
    assert outcome.status == SchedulerRunStatus.PREVIEWED
    record = service.history.get(outcome.agent_job_id)  # type: ignore[attr-defined]
    assert record is not None and record.permission_level == 1


def test_policy_deny_stays_deny(tmp_path: Path) -> None:
    # remove_tags for a tag that is not present -> policy engine never allows
    # it; whatever M7 reject shape is raised (deny / invalid preflight), the
    # runner must stop at FAILED without writing.
    adapter = m8.FakeAdapter(REMOVE_MISSING_TAG_JSON)
    vault, service = _vault_and_service(tmp_path, adapter=adapter)
    runner = _runner(service)
    outcome = asyncio.run(runner.arun("daily_organizer", request_auto_level2=None))
    assert outcome.status == SchedulerRunStatus.FAILED
    assert outcome.error_code in ("policy_denied", "invalid_action")
    before = vault.read_bytes("notes/a.md")[0]
    assert b"nope" not in before


def test_ai_offline_fails_safely(tmp_path: Path) -> None:
    from server.ai.schemas import AICapabilities, DiscoveredModel

    class OfflineAdapter:
        async def list_models(self):
            return [DiscoveredModel(id="mock-qwen", capabilities=AICapabilities(chat=True))]

        async def chat(self, **kwargs):
            raise RuntimeError("model offline")

    vault = m7.make_vault(tmp_path, {"notes/a.md": b"# A\n"})
    service = m7.make_service(vault, adapter=OfflineAdapter(), default_model="mock-qwen")
    runner = _runner(service)
    outcome = asyncio.run(runner.arun("daily_organizer"))
    assert outcome.status == SchedulerRunStatus.FAILED
    assert outcome.error_code in ("ai_unavailable", "scheduler_unavailable")
    assert outcome.message


# ---------------------------------------------------------------------------
# Level 2 (explicit config + policy allow + tag-only whitelist)
# ---------------------------------------------------------------------------


def _level2_service(tmp_path: Path, *, actions=(), content=GOOD_ACTIONS):
    adapter = m8.FakeAdapter(content)
    vault = m7.make_vault(
        tmp_path,
        {"notes/a.md": b"---\ntags:\n  - work\n---\n# A\n\nbody\n"},
    )
    history = m7.make_history(tmp_path)
    policy = PolicyEngine(
        level2_auto_actions={ActionType(item) for item in actions},
        level2_max_files=1,
        max_level2_modified_chars=2000,
    )
    service = m7.make_service(
        vault,
        history=history,
        policy=policy,
        adapter=adapter,
        default_model="mock-qwen",
    )
    return vault, service


def test_level2_whitelisted_tag_auto_commits_through_m7(tmp_path: Path) -> None:
    vault, service = _level2_service(tmp_path, actions=("add_tags",))
    runner = _runner(
        service,
        level2_enabled=True,
        level2_actions={ActionType.ADD_TAGS},
    )
    outcome = asyncio.run(runner.arun("daily_organizer", request_auto_level2=None))
    assert outcome.status == SchedulerRunStatus.COMMITTED
    record = service.history.get(outcome.agent_job_id)  # type: ignore[attr-defined]
    assert record is not None and record.status == "committed"
    assert record.permission_level == 2
    # write really happened through the M7 chain
    assert b"inbox" in vault.read_bytes("notes/a.md")[0]


def test_level2_empty_whitelist_never_auto(tmp_path: Path) -> None:
    # Server switch is on but the whitelist is empty -> Level 1 only.
    vault, service = _level2_service(tmp_path, actions=("add_tags",))
    runner = _runner(service, level2_enabled=True, level2_actions=set())
    outcome = asyncio.run(runner.arun("daily_organizer", request_auto_level2=None))
    assert outcome.status == SchedulerRunStatus.PREVIEWED
    record = service.history.get(outcome.agent_job_id)  # type: ignore[attr-defined]
    assert record is not None and record.permission_level == 1
    assert b"inbox" not in vault.read_bytes("notes/a.md")[0]


def test_weekly_create_note_never_level2_auto(tmp_path: Path) -> None:
    # Policy at Level 2 never allows create_note even when the workflow can.
    vault, service = _level2_service(tmp_path, actions=("add_tags",), content=WEEKLY_CREATE_JSON)
    runner = _runner(
        service,
        level2_enabled=True,
        level2_actions={ActionType.ADD_TAGS},
    )
    outcome = asyncio.run(runner.arun("weekly_review", request_auto_level2=None))
    assert outcome.status == SchedulerRunStatus.FAILED
    assert outcome.error_code == "policy_denied"
    assert not (vault.root / "notes/weekly.md").exists()


def test_level2_non_whitelisted_action_not_auto_committed(tmp_path: Path) -> None:
    # add_tags allowed by policy but outside the scheduler whitelist (patch_note
    # is only in the whitelist concept here -> the tag action must still not
    # auto-commit because the whitelist is per action-set).
    patch_json = (
        '{"actions":[{"action":"add_tags","file":"notes/a.md","tags":["todo"],'
        '"reason":"m8 test"}],"task_type":"daily_organizer"}'
    )
    vault, service = _level2_service(tmp_path, actions=("add_tags",), content=patch_json)
    runner = _runner(
        service,
        level2_enabled=True,
        level2_actions={ActionType.REMOVE_TAGS},  # server whitelist excludes add_tags
    )
    outcome = asyncio.run(runner.arun("daily_organizer", request_auto_level2=None))
    assert outcome.status == SchedulerRunStatus.FAILED
    assert "whitelist" in (outcome.message or "")
    assert b"todo" not in vault.read_bytes("notes/a.md")[0]


def test_level1_accept_and_undo_contract_unchanged(tmp_path: Path) -> None:
    from server.agents.schemas import AcceptJobRequest, UndoJobRequest

    adapter = m8.FakeAdapter(GOOD_ACTIONS)
    vault, service = _vault_and_service(tmp_path, adapter=adapter)
    runner = _runner(service)
    outcome = asyncio.run(runner.arun("daily_organizer", request_auto_level2=None))
    job_id = outcome.agent_job_id
    assert job_id
    # the same public accept entry the UI uses
    committed = service.accept(job_id, AcceptJobRequest(confirm=True, action_ids=[]))
    assert committed["status"] == "committed"
    undone = service.undo(job_id, UndoJobRequest(confirm=True))
    assert undone["status"] == "undone"
    assert b"inbox" not in vault.read_bytes("notes/a.md")[0]


def test_unknown_task_rejected_by_runner(tmp_path: Path) -> None:
    _vault, service = _vault_and_service(tmp_path, adapter=m8.FakeAdapter(GOOD_ACTIONS))
    outcome = asyncio.run(JobRunner(service).arun("not_a_task"))  # type: ignore[arg-type]
    assert outcome.status == SchedulerRunStatus.FAILED
    assert outcome.error_code == "unknown_task"


def test_unknown_plan_exception_uses_fixed_safe_message(tmp_path: Path) -> None:
    """S3: a raw exception from the agent chain never leaks str(exc) into
    run.message — the runner records the fixed copy and only logs the cause."""

    class BoomService:
        async def plan(self, request):
            raise RuntimeError("raw upstream detail host 10.0.0.8 leak-abc")

    runner = JobRunner(BoomService())  # type: ignore[arg-type]
    outcome = asyncio.run(runner.arun("daily_organizer", request_auto_level2=False))
    assert outcome.status == SchedulerRunStatus.FAILED
    assert outcome.error_code == "scheduler_unavailable"
    message = outcome.message or ""
    assert "unexpected error during agent planning" in message
    assert "leak-abc" not in message and "10.0.0.8" not in message
