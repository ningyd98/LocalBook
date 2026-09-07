"""Controlled agent job service for M7 (PLAN-M7 §5.4/§5.6, M7-09/M7-10).

Flow: ``plan`` (validate -> at most one model call -> read-only preflight ->
policy -> diff, no writes), ``accept`` (re-preflight against current bytes ->
durable journal -> execute via VaultService -> inverse rollback on failure),
``undo`` (hash-guarded restore from the journal, no model involved).  Every
write funnels through :class:`server.vault.service.VaultService` via
:class:`server.recovery.executor.TransactionExecutor`.
"""

from __future__ import annotations

import base64
import threading
import uuid
from collections import defaultdict
from typing import Any

from server.actions.diff import build_diff, sha256
from server.actions.patches import UnsupportedPatch
from server.actions.schemas import Action, ActionType
from server.agents.registry import ToolRegistry
from server.agents.schemas import (
    AcceptJobRequest,
    JobRequest,
    TaskType,
    UndoJobRequest,
    create_target_problem,
)
from server.agents.tools import (
    ToolContext,
    plan_create_operation,
    plan_move_operation,
)
from server.agents.workflows import WORKFLOWS, WorkflowPlanner
from server.ai.adapters.base import ModelAdapter
from server.history.schemas import HistoryRecord
from server.history.service import HistoryService
from server.policies.engine import ActionFacts, PolicyDecision, PolicyEngine, PolicySetResult
from server.policies.errors import (
    AIUnavailable,
    ConfirmationRequired,
    HistoryLimitExceeded,
    InvalidAction,
    InvalidActionOutput,
    JobNotFound,
    JobStateConflict,
    PolicyDenied,
    RollbackFailed,
    TransactionFailed,
    UndoConflict,
    UndoUnavailable,
)
from server.policies.rules import (
    DEFAULT_PROTECTED_PREFIXES,
    MARKDOWN_SUFFIXES,
    MAX_JOURNAL_BYTES,
)
from server.recovery.schemas import JobStatus, RecoveryResult, TransactionOperation
from server.recovery.service import RecoveryService

_PENDING_STATUSES = {"awaiting_confirmation"}


def _is_markdown(path: str) -> bool:
    return path.casefold().endswith(MARKDOWN_SUFFIXES)


def _tags_from_text(text: str) -> list[str]:
    try:
        from server.markdown.frontmatter import parse_frontmatter

        result = parse_frontmatter(text)
        return list(getattr(result, "tags", []) or [])
    except Exception:  # pragma: no cover - parser degradation
        return []


def _tags_only_change(before: str, after: str) -> bool:
    from server.actions.patches import tags_only_change

    return tags_only_change(before, after)


def _add_tags_bytes(before: str, tags: list[str]) -> bytes:
    from server.actions.patches import add_tags

    return add_tags(before, tags).encode("utf-8")


def _remove_tags_bytes(before: str, tags: list[str]) -> bytes:
    from server.actions.patches import remove_tags

    return remove_tags(before, tags).encode("utf-8")


def _add_link_bytes(before: str, target: str) -> bytes:
    from server.actions.patches import add_link

    return add_link(before, target).encode("utf-8")


class _Preflight:
    """Read-only snapshot: planned operations + facts + diffs for one set."""

    def __init__(self) -> None:
        self.operations: list[TransactionOperation | None] = []
        self.facts: list[ActionFacts] = []
        self.diffs: list[dict[str, Any]] = []
        self.files_read: list[str] = []
        self.modified_chars = 0


class AgentJobService:
    """Orchestrates controlled jobs: plan -> policy -> diff -> execute -> undo."""

    def __init__(
        self,
        vault: Any,
        history: HistoryService | None = None,
        policy: PolicyEngine | None = None,
        recovery: RecoveryService | None = None,
        adapter: ModelAdapter | None = None,
        *,
        registry: ToolRegistry | None = None,
        planner: WorkflowPlanner | None = None,
        tool_context: ToolContext | None = None,
        settings: Any = None,
        default_model: str = "local-model",
        protected_prefixes: tuple[str, ...] = DEFAULT_PROTECTED_PREFIXES,
        max_journal_bytes: int = MAX_JOURNAL_BYTES,
    ) -> None:
        self.vault = vault
        self.history = history or HistoryService()
        self.policy = policy or PolicyEngine()
        self.recovery = recovery or RecoveryService(vault, self.history)
        self.adapter = adapter
        self.registry = registry or ToolRegistry()
        self.planner = planner or WorkflowPlanner(self.registry)
        self.tool_context = tool_context or ToolContext(vault=vault)
        self.settings = settings
        self.default_model = default_model
        self.protected_prefixes = tuple(protected_prefixes)
        self.max_journal_bytes = int(max_journal_bytes)
        self._locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
        self._jobs: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Model resolution
    # ------------------------------------------------------------------

    async def _resolve_model(self) -> str:
        configured = getattr(self.settings, "chat_model", None) if self.settings else None
        if configured and configured != "auto":
            return configured
        if self.adapter is None:
            raise AIUnavailable("AI planner is unavailable")
        try:
            models = await self.adapter.list_models()
        except Exception as exc:
            raise AIUnavailable("AI model discovery failed") from exc
        for model in models:
            capabilities = getattr(model, "capabilities", None)
            if capabilities is None or getattr(capabilities, "chat", False):
                return getattr(model, "id", "") or self.default_model
        raise AIUnavailable("No chat-capable model is available")

    # ------------------------------------------------------------------
    # Planning
    # ------------------------------------------------------------------

    async def plan(self, request: JobRequest) -> dict[str, Any]:
        """Create a preview job (no writes unless Level-2 allow + execute)."""
        request = request.validated()
        level = int(request.permission_level)
        seeded = request.actions
        model: str | None = None
        prompt_version: str | None = None
        if seeded:
            actions = [self._stamp(action, level) for action in seeded]
            self._enforce_task_actions(request.task_type, actions)
            model = self.default_model
        else:
            if request.task_type == "manual":
                raise InvalidAction("manual jobs require explicit actions")
            if self.adapter is None or self.tool_context is None:
                raise AIUnavailable("AI planner is unavailable")
            model = await self._resolve_model()
            settings = self.settings
            actionset = await self.planner.plan(
                task=request.task_type,
                request=request,
                adapter=self.adapter,
                tool_context=self.tool_context,
                model=model,
                temperature=getattr(settings, "temperature", 0.1) if settings else 0.1,
                timeout_seconds=(
                    getattr(settings, "request_timeout_seconds", 2.0) if settings else 2.0
                ),
                max_output_tokens=(
                    getattr(settings, "max_output_tokens", 1200) if settings else 1200
                ),
            )
            actions = [self._stamp(action, level) for action in actionset.actions]
            model = actionset.model or model
            prompt_version = actionset.prompt_version

        job_id = str(uuid.uuid4())
        self._assert_scope_paths(request, actions)
        preflight = self._preflight(actions, level=level)
        self._assert_no_read_failures(preflight)
        set_result = self.policy.evaluate_set(
            actions, facts=preflight.facts, level=level
        )
        if set_result.decision == PolicyDecision.DENY:
            self._record_denied(job_id, request, actions, preflight, set_result, level)
            raise PolicyDenied(
                "Policy denied the action set",
                meta={"job_id": job_id, "rules": set_result.matched_rules},
            )
        record = self._record_job(
            job_id=job_id,
            request=request,
            actions=actions,
            preflight=preflight,
            status=JobStatus.AWAITING_CONFIRMATION,
            level=level,
            model=model,
            prompt_version=prompt_version,
        )
        policy_view = self._policy_view(set_result)
        self._cache(job_id, record, policy_view)
        if request.execute and set_result.decision == PolicyDecision.ALLOW:
            return self._execute_locked(
                job_id, confirm=True, action_ids=[str(action.action_id) for action in actions]
            )
        return self._detail_view(job_id)

    def _stamp(self, action: Action, level: int) -> Action:
        """Apply the server-side level; model-claimed levels are untrusted."""
        if action.permission_level != level:
            return action.model_copy(update={"permission_level": level})
        return action

    def _enforce_task_actions(self, task: TaskType, actions: list[Action]) -> None:
        meta = WORKFLOWS.get(task)
        if meta is None:
            return
        allowed = {item.value for item in meta.allowed_actions}
        for action in actions:
            if action.action.value not in allowed:
                raise InvalidActionOutput(
                    f"action {action.action.value} is not allowed by {task}",
                    meta={"action": action.action.value, "workflow": task},
                )

    def _assert_scope_paths(self, request: JobRequest, actions: list[Action]) -> None:
        scope_paths = [path.casefold() for path in request.scope.paths]
        if not scope_paths:
            return
        for action in actions:
            if action.action == ActionType.CREATE_NOTE:
                # A new note is never a member of the declared scope files;
                # it must instead be a safe new path inside the scope
                # directory (existence/overwrite is re-checked by preflight +
                # policy before any write).
                problem = create_target_problem(
                    action.file, scope_paths=request.scope.paths
                )
                if problem is not None:
                    raise InvalidAction(f"{problem}: {action.file}")
                continue
            if action.file.casefold() not in scope_paths:
                raise InvalidAction(f"{action.file} is outside the declared scope")
            if action.target_file and action.target_file.casefold() not in scope_paths:
                raise InvalidAction(f"{action.target_file} is outside the declared scope")

    # ------------------------------------------------------------------
    # Read-only preflight
    # ------------------------------------------------------------------

    def _preflight(self, actions: list[Action], *, level: int) -> _Preflight:
        preflight = _Preflight()
        distinct_files: set[str] = set()
        for action in actions:
            mutated = {action.file}
            if action.action == ActionType.MOVE_NOTE and action.target_file:
                mutated.add(action.target_file)
            distinct_files.update(mutated)
            facts, operation = self._preflight_one(action)
            preflight.operations.append(operation)
            preflight.facts.append(facts)
        for action, operation, facts in zip(
            actions, preflight.operations, preflight.facts, strict=False
        ):
            entry = self._diff_entry(action, operation, facts)
            if entry is not None:
                preflight.diffs.append(entry)
        preflight.files_read = sorted(distinct_files)
        for facts in preflight.facts:
            facts.file_count = len(distinct_files)
        return preflight

    def _assert_no_read_failures(self, preflight: _Preflight) -> None:
        """A mutated non-UTF-8 file can never produce a safe diff."""
        for facts, diff in zip(preflight.facts, preflight.diffs, strict=False):
            if facts.existing and not facts.markdown and diff.get("operation") == "update":
                raise InvalidAction("non-Markdown files cannot be rewritten")

    def _preflight_one(
        self, action: Action
    ) -> tuple[ActionFacts, TransactionOperation | None]:
        path = action.file
        target = action.target_file
        link_target = action.link_target
        facts = ActionFacts(
            existing=True,
            protected=self._protected(path)
            or self._protected(target or "")
            or self._protected(link_target or ""),
            scope="single-file",
            markdown=_is_markdown(path),
            attachment=not _is_markdown(path),
        )
        data, digest = self._try_read(path)
        if digest is None:
            facts.existing = False
        if action.action == ActionType.CREATE_NOTE:
            return self._preflight_create(action, facts)
        if action.action == ActionType.MOVE_NOTE:
            return self._preflight_move(action, facts, data, digest)
        return self._preflight_update(action, facts, data, digest, link_target)

    def _try_read(self, path: str) -> tuple[bytes, str | None]:
        try:
            data, digest = self.vault.read_bytes(path)
            return data, digest
        except Exception:
            return b"", None

    def _preflight_create(
        self, action: Action, facts: ActionFacts
    ) -> tuple[ActionFacts, TransactionOperation | None]:
        try:
            content = base64.b64decode(action.content_base64 or "")
            text = content.decode("utf-8")
        except Exception as exc:
            raise InvalidAction("create content is not valid UTF-8 text") from exc
        facts.modified_chars = len(text)
        if facts.existing:
            return facts, None
        return facts, plan_create_operation(action)

    def _preflight_move(
        self,
        action: Action,
        facts: ActionFacts,
        data: bytes,
        digest: str | None,
    ) -> tuple[ActionFacts, TransactionOperation | None]:
        target = action.target_file
        target_md = _is_markdown(target) if target else False
        facts.markdown = _is_markdown(action.file) and target_md
        facts.attachment = not (facts.markdown)
        target_exists = bool(target and self._try_read(target)[1])
        facts.target_exists = target_exists
        facts.overwrite = target_exists
        if (
            target is None
            or not facts.existing
            or target_exists
            or not target_md
            or digest is None
        ):
            return facts, None
        return facts, plan_move_operation(action, action.file, target, data, digest)

    def _preflight_update(
        self,
        action: Action,
        facts: ActionFacts,
        data: bytes,
        digest: str | None,
        link_target: str | None,
    ) -> tuple[ActionFacts, TransactionOperation | None]:
        kind = action.action
        if kind == ActionType.ADD_LINK:
            facts.markdown = _is_markdown(action.file) and _is_markdown(link_target or "")
            facts.attachment = not facts.markdown
        if not facts.existing or facts.attachment or not facts.markdown or digest is None:
            return facts, None
        try:
            before = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise InvalidAction("note is not UTF-8 text") from exc
        after_bytes = data
        try:
            if kind in (ActionType.ADD_TAGS, ActionType.REMOVE_TAGS):
                current_tags = {tag.casefold() for tag in _tags_from_text(before)}
                requested = {tag.casefold() for tag in action.tags}
                if kind == ActionType.ADD_TAGS:
                    facts.duplicate_tags = bool(requested & current_tags)
                    after_bytes = _add_tags_bytes(before, action.tags)
                else:
                    facts.tags_present = requested <= current_tags
                    after_bytes = _remove_tags_bytes(before, action.tags)
                after_text = after_bytes.decode("utf-8")
                facts.body_change = not _tags_only_change(before, after_text)
                facts.modified_chars = abs(len(after_text) - len(before))
            elif kind == ActionType.ADD_LINK:
                if link_target is None:
                    raise InvalidAction("add_link requires a target")
                facts.link_target_exists = self._try_read(link_target)[1] is not None
                after_bytes = _add_link_bytes(before, link_target)
                facts.body_change = True
                facts.modified_chars = abs(len(after_bytes) - len(data))
            elif kind == ActionType.PATCH_NOTE:
                after_bytes = self._apply_hunks(action, data)
                facts.body_change = True
                facts.modified_chars = abs(len(after_bytes) - len(data))
        except UnsupportedPatch as exc:
            raise UnsupportedPatchError(str(exc)) from exc
        return (
            facts,
            TransactionOperation(
                operation="update",
                path=action.file,
                before_exists=True,
                before_bytes=data,
                before_hash=digest,
                after_bytes=after_bytes,
                after_hash=None,
            ),
        )

    @staticmethod
    def _apply_hunks(action: Action, data: bytes) -> bytes:
        from server.actions.patches import apply_hunks

        return apply_hunks(data, action.patch)

    def _protected(self, path: str) -> bool:
        if not path:
            return False
        lowered = path.casefold()
        return any(
            lowered == prefix or lowered.startswith(prefix + "/")
            for prefix in self.protected_prefixes
        )

    def _diff_entry(
        self,
        action: Action,
        operation: TransactionOperation | None,
        facts: ActionFacts,
    ) -> dict[str, Any] | None:
        """Immutable diff metadata for one proposed action (never writes)."""
        path = action.file
        if action.action == ActionType.CREATE_NOTE:
            after = operation.after_bytes if operation is not None else None
            return {
                "path": path,
                "action_id": str(action.action_id),
                "operation": "create",
                "before_hash": None,
                "after_hash": sha256(after) if after is not None else None,
                "before_size": 0,
                "after_size": len(after) if after is not None else 0,
                "unified_diff": None,
                "status": "proposed",
            }
        before = operation.before_bytes if operation is not None else None
        after = operation.after_bytes if operation is not None else None
        if action.action == ActionType.MOVE_NOTE:
            target = action.target_file or ""
            return {
                "path": path,
                "action_id": str(action.action_id),
                "operation": "move",
                "target_file": target,
                "before_hash": sha256(before) if before is not None else None,
                "after_hash": sha256(after) if after is not None else None,
                "before_size": len(before) if before is not None else 0,
                "after_size": len(after) if after is not None else 0,
                "unified_diff": None,
                "status": "proposed",
            }
        if before is None:
            return None
        diff = build_diff(path, before, after or b"")
        return {
            "path": path,
            "action_id": str(action.action_id),
            "operation": "update",
            "before_hash": diff.before_hash,
            "after_hash": diff.after_hash,
            "before_size": diff.before_size,
            "after_size": diff.after_size,
            "unified_diff": diff.unified_diff,
            "status": "proposed",
        }

    # ------------------------------------------------------------------
    # Accept / execute / reject / undo
    # ------------------------------------------------------------------

    def accept(self, job_id: str, body: AcceptJobRequest) -> dict[str, Any]:
        if not body.confirm:
            raise ConfirmationRequired("Explicit confirmation is required")
        with self._locks[job_id]:
            return self._execute_locked(
                job_id, confirm=True, action_ids=list(body.action_ids)
            )

    def _execute_locked(
        self,
        job_id: str,
        *,
        confirm: bool,
        action_ids: list[str],
    ) -> dict[str, Any]:
        if not confirm:
            raise ConfirmationRequired("Explicit confirmation is required")
        job = self._load(job_id)
        record = job["record"]
        if record.status not in _PENDING_STATUSES:
            raise JobStateConflict("job is not awaiting confirmation")
        proposed = self._actions_from_record(record)
        selected = self._select_actions(proposed, action_ids, job_id)
        level = int(record.permission_level)
        preflight = self._preflight(selected, level=level)
        self._assert_preview_hashes(job_id, record, selected)
        set_result = self.policy.evaluate_set(selected, facts=preflight.facts, level=level)
        if set_result.decision == PolicyDecision.DENY:
            raise JobStateConflict("files changed since the preview; policy now denies")
        operations = [
            operation for operation in preflight.operations if operation is not None
        ]
        if not operations:
            raise JobStateConflict("nothing to execute")
        total_journal = sum(len(op.before_bytes or b"") for op in operations)
        if total_journal > self.max_journal_bytes:
            raise HistoryLimitExceeded(
                "Journal before-state exceeds the size limit",
                meta={"job_id": job_id, "bytes": total_journal},
            )
        self.history.save(
            record.model_copy(update={"status": JobStatus.EXECUTING.value})
        )
        result = self.recovery.execute(job_id, operations)
        if result.status == JobStatus.COMMITTED:
            return self._commit(job_id, record, selected, preflight, set_result, level)
        if result.status == JobStatus.ROLLED_BACK:
            self._record_terminal(job_id, record, JobStatus.ROLLED_BACK, result)
            raise TransactionFailed(
                result.error or "transaction failed", meta={"job_id": job_id}
            )
        if result.status == JobStatus.ROLLBACK_FAILED:
            self._record_terminal(job_id, record, JobStatus.ROLLBACK_FAILED, result)
            raise RollbackFailed(
                result.error or "rollback failed", meta={"job_id": job_id}
            )
        raise JobStateConflict(
            result.error or "file conflict during execution", meta={"job_id": job_id}
        )


    def _assert_preview_hashes(
        self,
        job_id: str,
        record: HistoryRecord,
        actions: list[Action],
    ) -> None:
        """Reject a stale accept: every mutated file must still match its
        preview before-hash.  The UI diff is never trusted as current."""
        for action in actions:
            expected = record.before_hash.get(action.file)
            if expected is None:
                continue  # create_note: existence re-checked by policy facts
            try:
                _data, digest = self.vault.read_bytes(action.file)
            except Exception as exc:
                raise JobStateConflict(
                    f"file changed since the preview: {action.file}",
                    meta={"job_id": job_id, "path": action.file},
                ) from exc
            if digest != expected:
                raise JobStateConflict(
                    f"file changed since the preview: {action.file}",
                    meta={"job_id": job_id, "path": action.file},
                )

    def _commit(
        self,
        job_id: str,
        record: HistoryRecord,
        selected: list[Action],
        preflight: _Preflight,
        set_result: PolicySetResult,
        level: int,
    ) -> dict[str, Any]:
        executed_ids = {str(action.action_id) for action in selected}
        selected_ids = {str(action.action_id) for action in selected}
        remaining = [
            action
            for action in self._actions_from_record(record)
            if str(action.action_id) not in selected_ids
        ]
        executed_actions = [action.model_dump(mode="json") for action in selected]
        new_diffs: list[dict[str, Any]] = []
        for entry in record.diff:
            if entry.get("action_id") in executed_ids:
                new_diffs.append({**entry, "status": "accepted"})
            else:
                new_diffs.append(entry)
        after_hash: dict[str, str | None] = dict(record.after_hash)
        for action, operation in zip(selected, preflight.operations, strict=False):
            if operation is not None:
                after = operation.after_hash
                if after is None and operation.after_bytes is not None:
                    after = sha256(operation.after_bytes)
                if after is not None:
                    after_hash[action.file] = after
        status = (
            JobStatus.COMMITTED.value
            if not remaining
            else JobStatus.AWAITING_CONFIRMATION.value
        )
        updated = record.model_copy(
            update={
                "status": status,
                "executed_actions": [*record.executed_actions, *executed_actions],
                "diff": new_diffs,
                "after_hash": after_hash,
            }
        )
        updated = self.history.save(updated)
        self._cache(job_id, updated, self._policy_view(set_result))
        return self._detail_view(job_id)

    def reject(self, job_id: str) -> dict[str, Any]:
        with self._locks[job_id]:
            job = self._load(job_id)
            record = job["record"]
            if record.status not in _PENDING_STATUSES:
                raise JobStateConflict("job is not awaiting confirmation")
            if record.executed_actions:
                raise JobStateConflict("cannot reject a job that already executed actions")
            updated = self.history.finish(record, status=JobStatus.REJECTED.value)
            self._cache(job_id, updated, job.get("policy"))
            return self._detail_view(job_id)

    def undo(self, job_id: str, body: UndoJobRequest) -> dict[str, Any]:
        if not body.confirm:
            raise ConfirmationRequired("Explicit confirmation is required")
        with self._locks[job_id]:
            job = self._load(job_id)
            record = job["record"]
            if record.status == JobStatus.UNDONE.value:
                raise UndoUnavailable("job has already been undone")
            if record.status != JobStatus.COMMITTED.value:
                raise UndoUnavailable("only committed jobs can be undone")
            result = self.recovery.undo(job_id)
            return self._finish_undo(job_id, record, result)

    def _finish_undo(
        self,
        job_id: str,
        record: HistoryRecord,
        result: RecoveryResult,
    ) -> dict[str, Any]:
        if result.status == JobStatus.UNDONE:
            updated = self.history.finish(record, status=JobStatus.UNDONE.value)
            self._cache(job_id, updated, None)
            return {
                "job_id": job_id,
                "status": "undone",
                "restored": result.restored,
                "error": None,
            }
        if result.status == JobStatus.UNDO_UNAVAILABLE:
            raise UndoUnavailable(result.error or "undo is unavailable")
        raise UndoConflict(
            result.error or "files changed since the job ran",
            meta={"job_id": job_id, "conflict_paths": result.conflict_paths},
        )

    # ------------------------------------------------------------------
    # Lookup / projection
    # ------------------------------------------------------------------

    def get_job(self, job_id: str) -> dict[str, Any]:
        self._load(job_id)
        dto = self._detail_view(job_id)
        dto["journal_summary"] = self._journal_summary(job_id)
        return dto

    def _journal_summary(self, job_id: str) -> list[dict[str, Any]]:
        """Bytes-free journal projection for the history detail endpoint.

        Reports per-step seq/operation/path/existence/hash/state only; the
        stored before-state bytes stay server-side (they are Undo's recovery
        material, never shipped to the UI).
        """
        try:
            entries = self.history.journal(job_id)
        except Exception:  # pragma: no cover - defensive: history unavailable
            return []
        summary: list[dict[str, Any]] = []
        for entry in entries:
            summary.append(
                {
                    "seq": entry.seq,
                    "operation": entry.operation,
                    "path": entry.path,
                    "before_exists": entry.before_exists,
                    "after_exists": entry.after_exists,
                    "before_hash": entry.before_hash,
                    "after_hash": entry.after_hash,
                    "state": entry.state,
                }
            )
        return summary

    def list_jobs(
        self, *, limit: int = 20, offset: int = 0, status: str | None = None
    ) -> dict[str, Any]:
        page = self.history.page(limit=limit, offset=offset, status=status)
        return {
            "items": [self.history.summary_dto(item) for item in page.items],
            "total": page.total,
            "limit": page.limit,
            "offset": page.offset,
        }

    def _load(self, job_id: str) -> dict[str, Any]:
        cached = self._jobs.get(job_id)
        if cached is not None:
            return cached
        record = self.history.get(job_id)
        if record is None:
            raise JobNotFound("job not found")
        self._cache(job_id, record, None)
        return self._jobs[job_id]

    def _cache(
        self,
        job_id: str,
        record: HistoryRecord,
        policy_view: dict[str, Any] | None,
    ) -> None:
        self._jobs[job_id] = {"record": record, "policy": policy_view}

    def _detail_view(self, job_id: str) -> dict[str, Any]:
        job = self._jobs.get(job_id)
        record = job["record"] if job else self.history.get(job_id)
        if record is None:
            raise JobNotFound("job not found")
        dto = self.history.detail_dto(record)
        dto["policy"] = job.get("policy") if job else None
        return dto

    @staticmethod
    def _policy_view(result: PolicySetResult) -> dict[str, Any]:
        return {
            "decision": result.decision.value,
            "matched_rules": result.matched_rules,
            "reasons": result.reasons,
        }

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _record_job(
        self,
        *,
        job_id: str,
        request: JobRequest,
        actions: list[Action],
        preflight: _Preflight,
        status: JobStatus,
        level: int,
        model: str | None,
        prompt_version: str | None,
    ) -> HistoryRecord:
        before_hash: dict[str, str | None] = {}
        after_hash: dict[str, str | None] = {}
        for diff in preflight.diffs:
            before_hash[diff["path"]] = diff["before_hash"]
            after_hash[diff["path"]] = diff["after_hash"]
        return self.history.record(
            job_id=job_id,
            task_type=request.task_type,
            status=status.value,
            permission_level=level,
            model=model,
            prompt_version=prompt_version,
            files_read=preflight.files_read,
            proposed_actions=[action.model_dump(mode="json") for action in actions],
            diff=[
                {**diff, "status": "proposed"}
                for diff in preflight.diffs
            ],
            before_hash=before_hash,
            after_hash=after_hash,
        )

    def _record_denied(
        self,
        job_id: str,
        request: JobRequest,
        actions: list[Action],
        preflight: _Preflight,
        result: PolicySetResult,
        level: int,
    ) -> None:
        record = self._record_job(
            job_id=job_id,
            request=request,
            actions=actions,
            preflight=preflight,
            status=JobStatus.FAILED,
            level=level,
            model=None,
            prompt_version=None,
        )
        finished = self.history.finish(
            record,
            status=JobStatus.FAILED.value,
            error={
                "code": "policy_denied",
                "message": "; ".join(result.reasons),
                "rules": result.matched_rules,
            },
        )
        self._cache(job_id, finished, self._policy_view(result))

    def _record_terminal(
        self,
        job_id: str,
        record: HistoryRecord,
        status: JobStatus,
        result: RecoveryResult,
    ) -> None:
        code = (
            "transaction_failed"
            if status != JobStatus.ROLLBACK_FAILED
            else "rollback_failed"
        )
        updated = self.history.finish(
            record,
            status=status.value,
            error={"code": code, "message": result.error or status.value},
        )
        self._cache(job_id, updated, None)

    @staticmethod
    def _actions_from_record(record: HistoryRecord) -> list[Action]:
        try:
            return [Action.model_validate(payload) for payload in record.proposed_actions]
        except Exception as exc:  # pragma: no cover - defensive
            raise InvalidAction("stored actions are unreadable") from exc

    @staticmethod
    def _select_actions(
        proposed: list[Action], requested_ids: list[str], job_id: str
    ) -> list[Action]:
        if not requested_ids:
            return list(proposed)
        normalized = {str(action_id) for action_id in requested_ids}
        known = {str(action.action_id) for action in proposed}
        unknown = normalized - known
        if unknown:
            raise InvalidAction(
                f"unknown action ids: {sorted(unknown)}", meta={"job_id": job_id}
            )
        return [action for action in proposed if str(action.action_id) in normalized]


class UnsupportedPatchError(InvalidAction):
    def __init__(self, message: str) -> None:
        super().__init__(message)


__all__ = [
    "AgentJobService",
    "HistoryRecord",
    "InvalidAction",
    "JobStatus",
    "UnsupportedPatchError",
    "plan_create_operation",
    "plan_move_operation",
]
