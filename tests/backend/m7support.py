"""Shared helpers for M7 backend tests (never collected by pytest)."""

from __future__ import annotations

import base64
import shutil
from pathlib import Path
from typing import Any

from server.agents.service import AgentJobService
from server.agents.schemas import JobRequest
from server.actions.schemas import Action, ActionType, PatchHunk
from server.history.repository import HistoryRepository
from server.history.service import HistoryService
from server.index.db import IndexDatabase
from server.policies.engine import PolicyEngine
from server.recovery.service import RecoveryService
from server.vault.service import VaultService

VAULT_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "vault"


def b64(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return base64.b64encode(data).decode("ascii")


def make_vault(root: Path, files: dict[str, bytes], *, max_file_bytes: int = 1 << 20) -> VaultService:
    """Fresh VaultService over ``root`` pre-populated with ``files``."""
    root.mkdir(parents=True, exist_ok=True)
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    service = VaultService(root, max_file_bytes=max_file_bytes, watcher_enabled=False)
    service.initialize(start_watcher=False)
    return service


def make_history(root: Path) -> HistoryService:
    """Repository-backed HistoryService on its own throwaway index.db."""
    derived = root / ".localnote"
    derived.mkdir(parents=True, exist_ok=True)
    db = IndexDatabase(derived / "index.db")
    db.open()
    return HistoryService(HistoryRepository(db))


def make_policy(*, level2_auto: tuple[ActionType, ...] = ()) -> PolicyEngine:
    return PolicyEngine(
        level2_auto_actions=set(level2_auto),
        level2_max_files=1,
        max_level2_modified_chars=2_000,
    )


def make_service(
    vault: VaultService,
    *,
    history: HistoryService | None = None,
    policy: PolicyEngine | None = None,
    adapter: Any = None,
    settings: Any = None,
    default_model: str = "mock-model",
    max_journal_bytes: int = 10_000_000,
    **kwargs: Any,
) -> AgentJobService:
    history = history or HistoryService()
    policy = policy or make_policy()
    return AgentJobService(
        vault,
        history=history,
        policy=policy,
        recovery=RecoveryService(vault, history),
        adapter=adapter,
        settings=settings,
        default_model=default_model,
        max_journal_bytes=max_journal_bytes,
        **kwargs,
    )


def make_job_request(
    *,
    actions: list[Action],
    level: int = 1,
    task_type: str = "manual",
    scope_paths: list[str] | None = None,
    execute: bool = False,
    max_files: int = 10,
) -> JobRequest:
    return JobRequest(
        task_type=task_type,  # type: ignore[arg-type]
        permission_level=level,
        scope={"paths": scope_paths or [], "max_files": max_files},
        actions=actions,
        execute=execute,
    )


def tag_action(kind: ActionType, path: str, tags: list[str], **extra: Any) -> Action:
    return Action(action=kind, file=path, tags=tags, reason="m7 test", **extra)


def create_action(path: str, content: bytes | str, **extra: Any) -> Action:
    return Action(
        action=ActionType.CREATE_NOTE,
        file=path,
        content_base64=b64(content),
        reason="m7 test",
        **extra,
    )


def update_action(path: str, before: bytes, after: bytes, **extra: Any) -> Action:
    """patch_note replacing the whole file text (byte-level exact hunk)."""
    old = before.decode("utf-8")
    new = after.decode("utf-8")
    hunk = PatchHunk(start=0, old_text=old, new_text=new)
    return Action(
        action=ActionType.PATCH_NOTE,
        file=path,
        patch=[hunk],
        reason="m7 test",
        expected_sha256=_sha_of(before),
        **extra,
    )


def move_action(source: str, target: str, source_bytes: bytes, **extra: Any) -> Action:
    return Action(
        action=ActionType.MOVE_NOTE,
        file=source,
        target_file=target,
        reason="m7 test",
        expected_sha256=_sha_of(source_bytes),
        **extra,
    )


def _sha_of(data: bytes) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(data).hexdigest()


def copy_fixture_vault(destination: Path) -> Path:
    target = destination / "fixture-vault"
    shutil.copytree(VAULT_FIXTURES, target, symlinks=True)
    return target
