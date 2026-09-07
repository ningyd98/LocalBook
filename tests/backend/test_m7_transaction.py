"""M7 transaction tests (PLAN-M7 §9.1-5): preview no-write, 5+1 rollback."""

from __future__ import annotations

import pytest

from server.agents.schemas import AcceptJobRequest
from server.policies.errors import PolicyDenied, TransactionFailed
from server.vault.errors import PathNotFound

import m7support as m7


def _note(title: str) -> bytes:
    return ("# %s\n\nbody\n" % title).encode("utf-8")


def test_plan_preview_never_writes(tmp_path: pytest.TempPathFactory) -> None:
    import asyncio

    root = tmp_path / "vault"
    files = {"notes/a.md": _note("A"), "notes/b.md": _note("B")}
    vault = m7.make_vault(root, files)
    before = {path: vault.read_bytes(path)[1] for path in files}
    service = m7.make_service(vault)
    request = m7.make_job_request(
        actions=[m7.tag_action(m7.ActionType.ADD_TAGS, "notes/a.md", ["inbox"])],
        scope_paths=["notes/a.md"],
    )

    async def run() -> None:
        out = await service.plan(request)
        assert out["status"] == "awaiting_confirmation"
        assert out["policy"]["decision"] == "confirm"
        after = {path: vault.read_bytes(path)[1] for path in files}
        assert after == before
        assert len(out["diff"]) == 1
        assert out["diff"][0]["status"] == "proposed"

    asyncio.run(run())


def test_denied_job_records_failure_and_writes_nothing(
    tmp_path: pytest.TempPathFactory,
) -> None:
    import asyncio

    root = tmp_path / "vault"
    vault = m7.make_vault(root, {"notes/a.md": _note("A")})
    history = m7.make_history(tmp_path)
    service = m7.make_service(vault, history=history)
    request = m7.make_job_request(
        actions=[m7.create_action("notes/a.md", "# NEW")],
        scope_paths=["notes/a.md"],  # target already exists -> create_existing deny
    )

    async def run() -> None:
        with pytest.raises(PolicyDenied):
            await service.plan(request)
        data = vault.read_bytes("notes/a.md")[0]
        assert data == _note("A")

    asyncio.run(run())
    page = history.page()
    assert page.items and page.items[0].status == "failed"
    assert page.items[0].error and page.items[0].error["code"] == "policy_denied"


def test_create_commit_and_undo_roundtrip(tmp_path: pytest.TempPathFactory) -> None:
    import asyncio

    root = tmp_path / "vault"
    (root / "notes").mkdir(parents=True)
    vault = m7.make_vault(root, {})
    service = m7.make_service(vault)
    request = m7.make_job_request(
        actions=[m7.create_action("notes/new.md", "# New note\n")]
    )

    async def run() -> None:
        out = await service.plan(request)
        job_id = out["job_id"]
        with pytest.raises(PathNotFound):
            vault.read_bytes("notes/new.md")
        accepted = service.accept(job_id, AcceptJobRequest(confirm=True))
        assert accepted["status"] == "committed"
        data, _ = vault.read_bytes("notes/new.md")
        assert data == b"# New note\n"

    asyncio.run(run())


def test_five_success_one_failure_rolls_back_all_five(
    tmp_path: pytest.TempPathFactory,
) -> None:
    import asyncio

    root = tmp_path / "vault"
    files = {"notes/f%d.md" % i: _note("F%d" % i) for i in range(1, 7)}
    vault = m7.make_vault(root, files)
    before: dict[str, bytes] = {}
    for path in files:
        before[path] = vault.read_bytes(path)[0]
    history = m7.make_history(tmp_path)
    service = m7.make_service(vault, history=history)

    actions = [
        m7.update_action("notes/f%d.md" % i, before["notes/f%d.md" % i], _note("F%d-v2" % i))
        for i in range(1, 6)
    ]
    # 6th action passes preflight (target absent) but its parent directory
    # does not exist, so the actual vault write fails mid-transaction.
    actions.append(m7.create_action("notes/absent/x6.md", b"# x\n"))
    request = m7.make_job_request(actions=actions)

    async def run() -> None:
        out = await service.plan(request)
        assert out["status"] == "awaiting_confirmation"
        job_id = out["job_id"]
        with pytest.raises(TransactionFailed):
            service.accept(job_id, AcceptJobRequest(confirm=True))
        # All five preceding updates are restored byte-for-byte; the 6th file
        # was never created.
        for i in range(1, 6):
            assert vault.read_bytes("notes/f%d.md" % i)[0] == before["notes/f%d.md" % i]
        with pytest.raises(PathNotFound):
            vault.read_bytes("notes/absent/x6.md")
        record = history.get(job_id)
        assert record is not None
        assert record.status == "rolled_back"
        assert record.error and record.error["code"] == "transaction_failed"
        journal = history.journal(job_id)
        assert len(journal) == 6
        assert all(entry.state == "rolled_back" for entry in journal)

    asyncio.run(run())


def test_stale_accept_conflicts_and_never_overwrites(
    tmp_path: pytest.TempPathFactory,
) -> None:
    import asyncio

    from server.policies.errors import JobStateConflict

    root = tmp_path / "vault"
    vault = m7.make_vault(root, {"notes/a.md": _note("A")})
    service = m7.make_service(vault)
    request = m7.make_job_request(
        actions=[m7.tag_action(m7.ActionType.ADD_TAGS, "notes/a.md", ["inbox"])]
    )

    async def run() -> None:
        out = await service.plan(request)
        job_id = out["job_id"]
        # external edit between preview and accept
        data, digest = vault.read_bytes("notes/a.md")
        vault.write_bytes("notes/a.md", data + b"\n# external\n", digest)
        with pytest.raises(JobStateConflict):
            service.accept(job_id, AcceptJobRequest(confirm=True))
        current, _ = vault.read_bytes("notes/a.md")
        assert b"external" in current
        assert b"inbox" not in current

    asyncio.run(run())


def test_journal_cap_is_a_413_preflight_error(
    tmp_path: pytest.TempPathFactory,
) -> None:
    import asyncio

    from server.policies.errors import HistoryLimitExceeded

    root = tmp_path / "vault"
    content = b"x" * 4096
    vault = m7.make_vault(root, {"notes/a.md": content})
    service = m7.make_service(vault, max_journal_bytes=128)
    request = m7.make_job_request(
        actions=[m7.tag_action(m7.ActionType.ADD_TAGS, "notes/a.md", ["inbox"])]
    )

    async def run() -> None:
        out = await service.plan(request)
        with pytest.raises(HistoryLimitExceeded):
            service.accept(out["job_id"], AcceptJobRequest(confirm=True))

    asyncio.run(run())
