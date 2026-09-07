"""M7 Undo tests (PLAN-M7 §9.1-9): no model, hash guards, audit rows."""

from __future__ import annotations

import asyncio

import pytest

from server.actions.schemas import ActionType
from server.agents.schemas import AcceptJobRequest, UndoJobRequest
from server.policies.errors import UndoConflict, UndoUnavailable
from server.vault.errors import PathNotFound

import m7support as m7


def _note(title: str) -> bytes:
    return ("# %s\n\nbody\n" % title).encode("utf-8")


def _commit_update_job(vault, history, path: str, tag: str) -> str:
    service = m7.make_service(vault, history=history)
    request = m7.make_job_request(
        actions=[m7.tag_action(ActionType.ADD_TAGS, path, [tag])]
    )

    async def run() -> str:
        out = await service.plan(request)
        assert out["status"] == "awaiting_confirmation"
        job_id = out["job_id"]
        assert service.accept(job_id, AcceptJobRequest(confirm=True))["status"] == "committed"
        return job_id

    return asyncio.run(run())


def test_undo_restores_and_records_audit(tmp_path: pytest.TempPathFactory) -> None:
    root = tmp_path / "vault"
    content = _note("A")
    vault = m7.make_vault(root, {"notes/a.md": content})
    history = m7.make_history(tmp_path)
    service = m7.make_service(vault, history=history)
    job_id = _commit_update_job(vault, history, "notes/a.md", "inbox")
    assert b"inbox" in vault.read_bytes("notes/a.md")[0]
    result = service.undo(job_id, UndoJobRequest(confirm=True))
    assert result["status"] == "undone"
    assert result["restored"] == ["notes/a.md"]
    assert vault.read_bytes("notes/a.md")[0] == content
    record = history.get(job_id)
    assert record is not None and record.status == "undone"
    # undo rows are journaled for audit
    ops = [entry.operation for entry in history.journal(job_id)]
    assert "undo" in ops


def test_duplicate_undo_is_unavailable(tmp_path: pytest.TempPathFactory) -> None:
    root = tmp_path / "vault"
    vault = m7.make_vault(root, {"notes/a.md": _note("A")})
    history = m7.make_history(tmp_path)
    service = m7.make_service(vault, history=history)
    job_id = _commit_update_job(vault, history, "notes/a.md", "inbox")
    assert service.undo(job_id, UndoJobRequest(confirm=True))["status"] == "undone"
    with pytest.raises(UndoUnavailable):
        service.undo(job_id, UndoJobRequest(confirm=True))


def test_undo_requires_confirm(tmp_path: pytest.TempPathFactory) -> None:
    from server.policies.errors import ConfirmationRequired

    root = tmp_path / "vault"
    vault = m7.make_vault(root, {"notes/a.md": _note("A")})
    history = m7.make_history(tmp_path)
    service = m7.make_service(vault, history=history)
    job_id = _commit_update_job(vault, history, "notes/a.md", "inbox")
    with pytest.raises(ConfirmationRequired):
        service.undo(job_id, UndoJobRequest(confirm=False))


def test_undo_conflict_refuses_to_overwrite_external_change(
    tmp_path: pytest.TempPathFactory,
) -> None:
    root = tmp_path / "vault"
    vault = m7.make_vault(root, {"notes/a.md": _note("A")})
    history = m7.make_history(tmp_path)
    service = m7.make_service(vault, history=history)
    job_id = _commit_update_job(vault, history, "notes/a.md", "inbox")
    # external editor rewrites the file after commit
    data, digest = vault.read_bytes("notes/a.md")
    vault.write_bytes("notes/a.md", data + b"\nexternal after commit\n", digest)
    with pytest.raises(UndoConflict):
        service.undo(job_id, UndoJobRequest(confirm=True))
    current, _ = vault.read_bytes("notes/a.md")
    assert b"external after commit" in current  # external bytes kept


def test_undo_multi_file_reverse_order(tmp_path: pytest.TempPathFactory) -> None:
    root = tmp_path / "vault"
    first = _note("F1")
    second = _note("F2")
    vault = m7.make_vault(root, {"notes/a.md": first, "notes/b.md": second})
    history = m7.make_history(tmp_path)
    service = m7.make_service(vault, history=history)
    request = m7.make_job_request(
        actions=[
            m7.tag_action(ActionType.ADD_TAGS, "notes/a.md", ["ta"]),
            m7.tag_action(ActionType.ADD_TAGS, "notes/b.md", ["tb"]),
        ]
    )

    async def run() -> str:
        out = await service.plan(request)
        job_id = out["job_id"]
        service.accept(job_id, AcceptJobRequest(confirm=True))
        return job_id

    job_id = asyncio.run(run())
    result = service.undo(job_id, UndoJobRequest(confirm=True))
    assert set(result["restored"]) == {"notes/a.md", "notes/b.md"}
    assert vault.read_bytes("notes/a.md")[0] == first
    assert vault.read_bytes("notes/b.md")[0] == second


def test_undo_create_removes_created_note(tmp_path: pytest.TempPathFactory) -> None:
    root = tmp_path / "vault"
    (root / "notes").mkdir(parents=True)
    vault = m7.make_vault(root, {})
    history = m7.make_history(tmp_path)
    service = m7.make_service(vault, history=history)
    request = m7.make_job_request(
        actions=[m7.create_action("notes/created.md", "# made\n")]
    )

    async def run() -> str:
        out = await service.plan(request)
        job_id = out["job_id"]
        service.accept(job_id, AcceptJobRequest(confirm=True))
        return job_id

    job_id = asyncio.run(run())
    assert vault.read_bytes("notes/created.md")[0] == b"# made\n"
    result = service.undo(job_id, UndoJobRequest(confirm=True))
    assert result["status"] == "undone"
    with pytest.raises(PathNotFound):
        vault.read_bytes("notes/created.md")


def test_undo_move_moves_file_back(tmp_path: pytest.TempPathFactory) -> None:
    root = tmp_path / "vault"
    content = _note("M")
    vault = m7.make_vault(root, {"notes/src.md": content})
    history = m7.make_history(tmp_path)
    service = m7.make_service(vault, history=history)
    request = m7.make_job_request(
        actions=[m7.move_action("notes/src.md", "notes/dst.md", content)]
    )

    async def run() -> str:
        out = await service.plan(request)
        job_id = out["job_id"]
        assert service.accept(job_id, AcceptJobRequest(confirm=True))["status"] == "committed"
        return job_id

    job_id = asyncio.run(run())
    with pytest.raises(PathNotFound):
        vault.read_bytes("notes/src.md")
    assert vault.read_bytes("notes/dst.md")[0] == content
    result = service.undo(job_id, UndoJobRequest(confirm=True))
    assert result["status"] == "undone"
    assert vault.read_bytes("notes/src.md")[0] == content
    with pytest.raises(PathNotFound):
        vault.read_bytes("notes/dst.md")


def test_undo_never_invokes_the_model(tmp_path: pytest.TempPathFactory) -> None:
    class SpyAdapter:
        def __init__(self) -> None:
            self.chat_calls = 0
            self.list_calls = 0

        async def list_models(self):  # pragma: no cover - never reached
            self.list_calls += 1
            return []

        async def chat(self, **kwargs):  # pragma: no cover - never reached
            self.chat_calls += 1
            raise AssertionError("model must not be called during undo")

    root = tmp_path / "vault"
    vault = m7.make_vault(root, {"notes/a.md": _note("A")})
    history = m7.make_history(tmp_path)
    spy = SpyAdapter()
    service = m7.make_service(vault, history=history, adapter=spy)
    # seeded/manual job: no chat at plan; undo must also never chat
    request = m7.make_job_request(
        actions=[m7.tag_action(ActionType.ADD_TAGS, "notes/a.md", ["x"])]
    )

    async def run() -> str:
        out = await service.plan(request)
        job_id = out["job_id"]
        service.accept(job_id, AcceptJobRequest(confirm=True))
        return job_id

    job_id = asyncio.run(run())
    result = service.undo(job_id, UndoJobRequest(confirm=True))
    assert result["status"] == "undone"
    assert spy.chat_calls == 0 and spy.list_calls == 0


def test_undo_unavailable_without_journal(tmp_path: pytest.TempPathFactory) -> None:
    root = tmp_path / "vault"
    vault = m7.make_vault(root, {"notes/a.md": _note("A")})
    history = m7.make_history(tmp_path)
    service = m7.make_service(vault, history=history)
    # a rejected job has no executed journal
    request = m7.make_job_request(
        actions=[m7.tag_action(ActionType.ADD_TAGS, "notes/a.md", ["x"])]
    )

    async def run() -> None:
        out = await service.plan(request)
        service.reject(out["job_id"])

    asyncio.run(run())
    records = history.page()
    assert records.items[0].status == "rejected"
    with pytest.raises(UndoUnavailable):
        service.undo(records.items[0].job_id, UndoJobRequest(confirm=True))
