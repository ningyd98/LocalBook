"""M7 controlled-agent tests (PLAN-M7 §9.1-10): tools, workflows, no loops."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from server.agents.registry import ToolRegistry
from server.agents.schemas import JobRequest
from server.agents.tools import ToolContext
from server.agents.workflows import WORKFLOWS
from server.ai.adapters.base import ChatResult
from server.ai.schemas import AICapabilities, DiscoveredModel
from server.policies.errors import InvalidAction, InvalidActionOutput
from server.vault.errors import PathNotFound

import m7support as m7

GOOD_ACTIONS = (
    '{"actions":[{"action":"add_tags","file":"notes/a.md","tags":["inbox"],'
    '"reason":"tidy"}],"task_type":"daily_organizer"}'
)


class FakeAdapter:
    def __init__(self, content: str = GOOD_ACTIONS) -> None:
        self.content = content
        self.chat_calls = 0
        self.list_calls = 0

    async def list_models(self):
        self.list_calls += 1
        return [DiscoveredModel(id="mock-qwen", capabilities=AICapabilities(chat=True))]

    async def chat(self, **kwargs) -> ChatResult:
        self.chat_calls += 1
        return ChatResult(content=self.content, model="mock-qwen")


def _vault_with_note(tmp_path=None) -> tuple[object, object]:
    import tempfile

    root = Path(tempfile.mkdtemp())
    vault = m7.make_vault(root, {"notes/a.md": b"# A\n\nbody\n"})
    registry = ToolRegistry()
    return vault, registry


def test_registry_metadata_is_static_and_complete() -> None:
    registry = ToolRegistry()
    names = set(registry.names())
    for read_tool in (
        "vault.list",
        "vault.read",
        "vault.search",
        "metadata.get",
        "knowledge.related",
        "backlinks",
        "outgoing",
        "keyword",
    ):
        assert read_tool in names
        assert registry.spec(read_tool) is not None
        assert registry.spec(read_tool).kind == "read"  # type: ignore[union-attr]
    for write_tool in (
        "note.create",
        "note.patch",
        "note.move",
        "metadata.set",
        "tag.add",
        "tag.remove",
        "link.add",
    ):
        assert write_tool in names
        spec = registry.spec(write_tool)
        assert spec is not None and spec.kind == "write"
        assert spec.allowed_workflows == frozenset()  # never workflow-callable


def test_every_workflow_has_read_only_allow_list() -> None:
    registry = ToolRegistry()
    for workflow in WORKFLOWS:
        allowed = registry.allowed_read_tools(workflow)
        for name in allowed:
            assert registry.spec(name).kind == "read"  # type: ignore[union-attr]
        write_names = {tool.name for tool in registry.write_tools()}
        assert not (set(allowed) & write_names)


def test_write_tool_direct_invoke_is_forbidden() -> None:
    registry = ToolRegistry()
    vault, _reg = _vault_with_note()
    ctx = ToolContext(vault=vault)
    for write_tool in ("note.create", "note.patch", "tag.add", "tag.remove", "metadata.set", "link.add", "note.move"):
        with pytest.raises(InvalidAction):
            registry.invoke("daily_organizer", write_tool, ctx, path="notes/a.md")


def test_unknown_tool_and_loop_patterns_are_forbidden() -> None:
    registry = ToolRegistry()
    ctx = ToolContext(vault=None)
    with pytest.raises(InvalidAction):
        registry.invoke("daily_organizer", "shell.exec", ctx, command="ls")
    with pytest.raises(InvalidAction):
        registry.invoke("daily_organizer", "http.fetch", ctx, url="https://example.com")
    with pytest.raises(InvalidAction):
        registry.invoke("daily_organizer", "vault.delete", ctx, path="notes/a.md")


def test_read_tools_only_touch_vault_and_are_bounded(tmp_path: pytest.TempPathFactory) -> None:
    registry = ToolRegistry()
    vault, _ = _vault_with_note(tmp_path)
    ctx = ToolContext(vault=vault)
    listed = registry.invoke("daily_organizer", "vault.list", ctx)
    assert listed == ["notes/a.md"]
    read = registry.invoke("daily_organizer", "vault.read", ctx, path="notes/a.md")
    assert read["content"].startswith("# A")
    assert read["sha256"].startswith("sha256:")


def test_daily_organizer_uses_exactly_one_model_call(
    tmp_path: pytest.TempPathFactory,
) -> None:
    registry = ToolRegistry()
    vault, _ = _vault_with_note(tmp_path)
    adapter = FakeAdapter()
    service = m7.make_service(
        vault, adapter=adapter, policy=m7.make_policy(), default_model="mock-qwen"
    )
    request = JobRequest(
        task_type="daily_organizer",
        permission_level=1,
        scope={"paths": [], "max_files": 5},
    )

    async def run() -> None:
        out = await service.plan(request)
        assert out["status"] == "awaiting_confirmation"
        assert out["model"] == "mock-qwen"
        assert adapter.chat_calls == 1
        assert len(out["diff"]) == 1

    asyncio.run(run())


def test_weekly_review_uses_exactly_one_model_call(
    tmp_path: pytest.TempPathFactory,
) -> None:
    registry = ToolRegistry()
    vault, _ = _vault_with_note(tmp_path)
    adapter = FakeAdapter()
    service = m7.make_service(vault, adapter=adapter, default_model="mock-qwen")
    request = JobRequest(
        task_type="weekly_review",
        permission_level=1,
        scope={"paths": ["notes/a.md"], "max_files": 5},
    )

    async def run() -> None:
        out = await service.plan(request)
        assert out["status"] == "awaiting_confirmation"
        assert adapter.chat_calls == 1

    asyncio.run(run())


def test_model_output_outside_scope_is_rejected_without_second_call(
    tmp_path: pytest.TempPathFactory,
) -> None:
    vault, _ = _vault_with_note(tmp_path)
    adapter = FakeAdapter(
        content=(
            '{"actions":[{"action":"add_tags","file":"notes/other.md",'
            '"tags":["x"],"reason":"out of scope"}],"task_type":"daily_organizer"}'
        )
    )
    service = m7.make_service(vault, adapter=adapter, default_model="mock-qwen")
    request = JobRequest(
        task_type="daily_organizer",
        permission_level=1,
        scope={"paths": ["notes/a.md"], "max_files": 5},
    )

    async def run() -> None:
        with pytest.raises(InvalidActionOutput):
            await service.plan(request)
        assert adapter.chat_calls == 1

    asyncio.run(run())


def test_garbage_model_output_is_502_invalid_action_output(
    tmp_path: pytest.TempPathFactory,
) -> None:
    vault, _ = _vault_with_note(tmp_path)
    adapter = FakeAdapter(content="sure, I will add the tag and rewrite everything")
    service = m7.make_service(vault, adapter=adapter, default_model="mock-qwen")
    request = JobRequest(
        task_type="daily_organizer",
        permission_level=1,
        scope={"paths": ["notes/a.md"], "max_files": 5},
    )

    async def run() -> None:
        with pytest.raises(InvalidActionOutput) as caught:
            await service.plan(request)
        assert caught.value.status_code == 502

    asyncio.run(run())


def test_action_type_beyond_workflow_allowlist_rejected(
    tmp_path: pytest.TempPathFactory,
) -> None:
    vault, _ = _vault_with_note(tmp_path)
    adapter = FakeAdapter(
        content=(
            '{"actions":[{"action":"create_note","file":"notes/n.md",'
            '"content_base64":"IyBuZXc=","reason":"nope"}],'
            '"task_type":"daily_organizer"}'
        )
    )
    service = m7.make_service(vault, adapter=adapter, default_model="mock-qwen")
    request = JobRequest(
        task_type="daily_organizer",
        permission_level=1,
        scope={"paths": ["notes/a.md"], "max_files": 5},
    )

    async def run() -> None:
        with pytest.raises(InvalidActionOutput):
            await service.plan(request)

    asyncio.run(run())


def test_manual_job_without_actions_is_rejected(
    tmp_path: pytest.TempPathFactory,
) -> None:
    vault, _ = _vault_with_note(tmp_path)
    service = m7.make_service(vault)
    request = JobRequest(task_type="manual", permission_level=1)

    async def run() -> None:
        with pytest.raises(InvalidAction):
            await service.plan(request)

    asyncio.run(run())


def test_no_agent_loop_after_acceptance(
    tmp_path: pytest.TempPathFactory,
) -> None:
    """Accepting/undoing never triggers further model calls (no loop)."""
    from server.agents.schemas import AcceptJobRequest, UndoJobRequest

    vault, _ = _vault_with_note(tmp_path)
    adapter = FakeAdapter()
    service = m7.make_service(vault, adapter=adapter, default_model="mock-qwen")
    request = JobRequest(
        task_type="daily_organizer",
        permission_level=1,
        scope={"paths": ["notes/a.md"], "max_files": 5},
    )

    async def run() -> None:
        out = await service.plan(request)
        job_id = out["job_id"]
        service.accept(job_id, AcceptJobRequest(confirm=True))
        assert adapter.chat_calls == 1
        service.undo(job_id, UndoJobRequest(confirm=True))
        assert adapter.chat_calls == 1

    asyncio.run(run())


def test_registry_spec_io_schema_and_cost_exist() -> None:
    registry = ToolRegistry()
    for spec in [*registry.read_tools(), *registry.write_tools()]:
        assert isinstance(spec.input_schema, dict)
        assert isinstance(spec.output_schema, dict)
        assert isinstance(spec.cost, int) and spec.cost >= 0
        assert spec.min_level >= 0


def _weekly_create_content(target: str) -> str:
    """A weekly_review ActionSet proposing exactly one new review note."""
    payload = {
        "actions": [
            {
                "action": "create_note",
                "file": target,
                "content_base64": m7.b64("# Week 36 Review\n\nReviewed everything.\n"),
                "reason": "create the weekly review",
            }
        ],
        "task_type": "weekly_review",
    }
    return json.dumps(payload)


def test_weekly_review_create_note_plan_accept_undo_flow(
    tmp_path: pytest.TempPathFactory,
) -> None:
    """Weekly Review may create one new note in the scope directory: plan
    previews it, accept writes it through VaultService, undo deletes it."""
    from server.agents.schemas import AcceptJobRequest, UndoJobRequest

    vault = m7.make_vault(
        tmp_path / "vault",
        {"notes/a.md": b"# A\nbody\n", "notes/b.md": b"# B\nbody\n"},
    )
    history = m7.make_history(tmp_path)
    adapter = FakeAdapter(content=_weekly_create_content("notes/weekly-review.md"))
    service = m7.make_service(
        vault, history=history, adapter=adapter, default_model="mock-qwen"
    )
    request = JobRequest(
        task_type="weekly_review",
        permission_level=1,
        scope={"paths": ["notes/a.md", "notes/b.md"], "max_files": 5},
    )

    async def run() -> None:
        out = await service.plan(request)
        assert out["status"] == "awaiting_confirmation"
        assert adapter.chat_calls == 1
        creates = [entry for entry in out["diff"] if entry["operation"] == "create"]
        assert len(creates) == 1
        assert creates[0]["path"] == "notes/weekly-review.md"
        assert creates[0]["status"] == "proposed"
        job_id = out["job_id"]
        committed = service.accept(job_id, AcceptJobRequest(confirm=True))
        assert committed["status"] == "committed"
        data, _digest = vault.read_bytes("notes/weekly-review.md")
        assert data.startswith(b"# Week 36 Review")
        # journal summary is served by the history detail projection and never
        # carries before-state bytes (S5).
        detail = service.get_job(job_id)
        journal = detail["journal_summary"]
        assert len(journal) == 1
        assert journal[0]["operation"] == "create"
        assert journal[0]["path"] == "notes/weekly-review.md"
        assert journal[0]["after_exists"] is True
        assert journal[0]["before_hash"] is None
        assert "before_bytes" not in journal[0]
        undone = service.undo(job_id, UndoJobRequest(confirm=True))
        assert undone["status"] == "undone"
        with pytest.raises(PathNotFound):
            vault.read_bytes("notes/weekly-review.md")
        assert service.get_job(job_id)["status"] == "undone"

    asyncio.run(run())


def test_weekly_review_create_note_outside_scope_dir_is_rejected(
    tmp_path: pytest.TempPathFactory,
) -> None:
    vault = m7.make_vault(tmp_path / "vault", {"notes/a.md": b"# A\nbody\n"})
    adapter = FakeAdapter(content=_weekly_create_content("journal/other.md"))
    service = m7.make_service(vault, adapter=adapter, default_model="mock-qwen")
    request = JobRequest(
        task_type="weekly_review",
        permission_level=1,
        scope={"paths": ["notes/a.md"], "max_files": 5},
    )

    async def run() -> None:
        with pytest.raises(InvalidActionOutput):
            await service.plan(request)
        assert adapter.chat_calls == 1  # rejected locally, never re-called
        with pytest.raises(PathNotFound):
            vault.read_bytes("journal/other.md")

    asyncio.run(run())


@pytest.mark.parametrize(
    "target",
    ["notes/a.md", "notes/notes.txt"],
    ids=["overwrite-existing", "non-markdown"],
)
def test_weekly_review_create_note_overwrite_and_non_markdown_rejected(
    tmp_path: pytest.TempPathFactory,
    target: str,
) -> None:
    vault = m7.make_vault(tmp_path / "vault", {"notes/a.md": b"# A\nbody\n"})
    adapter = FakeAdapter(content=_weekly_create_content(target))
    service = m7.make_service(vault, adapter=adapter, default_model="mock-qwen")
    request = JobRequest(
        task_type="weekly_review",
        permission_level=1,
        scope={"paths": ["notes/a.md"], "max_files": 5},
    )

    async def run() -> None:
        with pytest.raises(InvalidActionOutput):
            await service.plan(request)
        assert adapter.chat_calls == 1

    asyncio.run(run())


def test_manual_create_note_outside_scope_dir_is_rejected(
    tmp_path: pytest.TempPathFactory,
) -> None:
    """The service-level scope check exempts create targets the same way the
    workflow enforcer does: a new path must stay inside the scope directory."""
    vault = m7.make_vault(tmp_path / "vault", {"notes/a.md": b"# A\nbody\n"})
    service = m7.make_service(vault)
    action = m7.create_action("drafts/out.md", "# Draft\n", permission_level=1)
    request = m7.make_job_request(
        actions=[action],
        level=1,
        task_type="manual",
        scope_paths=["notes/a.md"],
    )

    async def run() -> None:
        with pytest.raises(InvalidAction):
            await service.plan(request)
        with pytest.raises(PathNotFound):
            vault.read_bytes("drafts/out.md")

    asyncio.run(run())
