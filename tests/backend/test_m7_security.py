"""M7 security tests (PLAN-M7 §9.1-6/12): paths, vault-only writes, leaks."""

from __future__ import annotations

import asyncio

import pytest

from server.actions.schemas import ActionType
from server.agents.schemas import AcceptJobRequest, JobRequest
from server.actions.schemas import ActionValidationError
from server.policies.errors import PolicyDenied

import m7support as m7


class RecordingVault:
    """Proxy asserting every write the job layer performs goes through vault."""

    ALLOWED_WRITES = {"create_bytes", "write_bytes", "move_file", "delete_file"}

    def __init__(self, vault) -> None:
        self._inner = vault
        self.calls: list[str] = []

    def __getattr__(self, name: str):
        def _proxy(*args, **kwargs):
            self.calls.append(name)
            return getattr(self._inner, name)(*args, **kwargs)

        return _proxy

    def read_bytes(self, path):
        self.calls.append("read_bytes")
        return self._inner.read_bytes(path)


def test_attachment_and_non_markdown_writes_are_policy_denied(
    tmp_path: pytest.TempPathFactory,
) -> None:
    vault = m7.make_vault(
        tmp_path / "vault",
        {"notes/photo.png": b"\x89PNG\r\n\x1a\n", "notes/a.md": b"# A\n"},
    )
    service = m7.make_service(vault)
    request = m7.make_job_request(
        actions=[m7.tag_action(ActionType.ADD_TAGS, "notes/photo.png", ["x"])]
    )

    async def run() -> None:
        with pytest.raises(PolicyDenied):
            await service.plan(request)

    asyncio.run(run())
    assert vault.read_bytes("notes/photo.png")[0] == b"\x89PNG\r\n\x1a\n"


def test_protected_prefix_denied_at_service_level(
    tmp_path: pytest.TempPathFactory,
) -> None:
    vault = m7.make_vault(tmp_path / "vault", {"notes/a.md": b"# A\n"})
    service = m7.make_service(vault)
    # .localnote is a protected prefix even though the file does not exist
    request = m7.make_job_request(
        actions=[m7.tag_action(ActionType.ADD_TAGS, ".localnote/x.md", ["x"])]
    )

    async def run() -> None:
        with pytest.raises(PolicyDenied):
            await service.plan(request)

    asyncio.run(run())


def test_all_job_writes_go_through_vaultservice_only(
    tmp_path: pytest.TempPathFactory,
) -> None:
    content = b"# A\n\nbody\n"
    vault = m7.make_vault(tmp_path / "vault", {"notes/a.md": content})
    spy = RecordingVault(vault)
    service = m7.make_service(spy)
    request = m7.make_job_request(
        actions=[m7.tag_action(ActionType.ADD_TAGS, "notes/a.md", ["inbox"])]
    )

    async def run() -> str:
        out = await service.plan(request)
        job_id = out["job_id"]
        service.accept(job_id, AcceptJobRequest(confirm=True))
        return job_id

    job_id = asyncio.run(run())
    service.undo(job_id, __import__("server.agents.schemas", fromlist=["UndoJobRequest"]).UndoJobRequest(confirm=True))
    writes = {name for name in spy.calls if name.startswith(("create", "write", "move", "delete"))}
    assert writes <= {"create_bytes", "write_bytes", "move_file", "delete_file"}
    assert "read_bytes" in spy.calls
    restored = spy._inner.read_bytes("notes/a.md")[0]
    assert restored == content


def test_outside_vault_and_traversal_never_reach_services(
    tmp_path: pytest.TempPathFactory,
) -> None:
    from server.vault.errors import VaultError

    vault = m7.make_vault(tmp_path / "vault", {"notes/a.md": b"# A\n"})
    service = m7.make_service(vault)
    sentinel = tmp_path / "sentinel.txt"
    sentinel.write_text("do not touch", encoding="utf-8")

    for bad_path in ("../sentinel.txt", "/tmp/evil.md", "notes/../sentinel.txt"):
        with pytest.raises((VaultError, ValueError, ActionValidationError)):
            action = m7.tag_action(ActionType.ADD_TAGS, bad_path, ["x"])
            m7.make_job_request(actions=[action])
        assert sentinel.read_text(encoding="utf-8") == "do not touch"


def test_no_secret_or_absolute_path_in_service_errors(
    tmp_path: pytest.TempPathFactory,
) -> None:
    vault = m7.make_vault(tmp_path / "vault", {"notes/a.md": b"# A\n"})
    history = m7.make_history(tmp_path)
    service = m7.make_service(vault, history=history)

    async def run() -> None:
        request = m7.make_job_request(
            actions=[m7.tag_action(ActionType.ADD_TAGS, ".localnote/x.md", ["x"])]
        )
        with pytest.raises(PolicyDenied) as caught:
            await service.plan(request)
        assert str(tmp_path) not in str(caught.value.message)
        page = history.page()
        assert page.items[0].error is not None
        assert str(tmp_path) not in str(page.items[0].error)

    asyncio.run(run())
