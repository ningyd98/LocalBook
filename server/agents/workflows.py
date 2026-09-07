"""Controlled agent workflows for M7 (PLAN-M7 §5.6, M7-09).

Daily Organizer and Weekly Review follow the same constrained pipeline:

1. validate the declared scope and gather context *only* through the
   registry's allowed read tools (every read goes through Vault/index
   service boundaries);
2. make at most **one** model request with a strict ActionSet JSON schema;
3. parse + semantically validate the ActionSet locally, enforce the workflow
   action allow-list and the scope path allow-list;
4. return exactly one ActionSet — no tool-call recursion, no agent loop.

Nothing in this module writes files; the job service turns the ActionSet into
journaled transactions only after policy/user confirmation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from server.actions.schemas import ActionSet, ActionType, validate_action_set
from server.actions.validators import parse_action_set
from server.agents.registry import ToolRegistry
from server.agents.schemas import JobRequest, JobScope, TaskType, create_target_problem
from server.agents.tools import ToolContext
from server.ai.adapters.base import ChatMessage, ModelAdapter
from server.ai.errors import AIAdapterError, AIError
from server.policies.errors import AIUnavailable, InvalidActionOutput
from server.policies.rules import MARKDOWN_SUFFIXES

PROMPT_VERSION_PREFIX = "m7.1"

_SYSTEM_PROMPT = (
    "You are a controlled note organizer. Return ONLY a strict JSON object "
    "matching the provided ActionSet schema. Do not output prose, markdown "
    "fences (unless the JSON itself), shell commands, or tool calls. "
    "Rules: no deletions, no overwriting existing files, no attachment "
    "changes, paths must be POSIX root-relative and drawn only from the "
    "context files; do not invent file paths; keep patch hunks tiny and "
    "anchored to the exact context text; every action needs a short reason; "
    "tag changes must not rewrite the body text."
)


@dataclass(frozen=True)
class WorkflowMeta:
    name: str
    description: str
    allowed_actions: tuple[ActionType, ...]
    allowed_read_tools: tuple[str, ...]


WORKFLOWS: dict[str, WorkflowMeta] = {
    "daily_organizer": WorkflowMeta(
        name="daily_organizer",
        description=(
            "Organize the provided notes for today: suggest additive tags, "
            "remove stale explicit tags, add a small number of wikilinks "
            "between related notes, and (rarely) propose one tiny patch that "
            "fixes a heading or to-do marker."
        ),
        allowed_actions=(
            ActionType.ADD_TAGS,
            ActionType.REMOVE_TAGS,
            ActionType.ADD_LINK,
            ActionType.PATCH_NOTE,
        ),
        allowed_read_tools=(
            "vault.list",
            "vault.read",
            "vault.search",
            "metadata.get",
            "knowledge.related",
            "backlinks",
            "outgoing",
            "keyword",
        ),
    ),
    "weekly_review": WorkflowMeta(
        name="weekly_review",
        description=(
            "Review the week's notes: propose tags, remove stale tags, link "
            "related notes, and optionally create ONE new review note under "
            "notes/ that summarizes the week (content must be brand new)."
        ),
        allowed_actions=(
            ActionType.ADD_TAGS,
            ActionType.REMOVE_TAGS,
            ActionType.ADD_LINK,
            ActionType.PATCH_NOTE,
            ActionType.CREATE_NOTE,
        ),
        allowed_read_tools=(
            "vault.list",
            "vault.read",
            "vault.search",
            "metadata.get",
            "knowledge.related",
            "backlinks",
            "outgoing",
            "keyword",
        ),
    ),
}


@dataclass
class WorkflowContext:
    """Tool context + budget used while a workflow gathers its context."""

    tool_context: ToolContext
    workflow: str
    allowed_tools: list[str] = field(default_factory=list)
    budget: int = 200


class WorkflowPlanner:
    """Runs one bounded workflow to produce exactly one validated ActionSet."""

    def __init__(self, registry: ToolRegistry | None = None) -> None:
        self.registry = registry or ToolRegistry()

    # ------------------------------------------------------------------
    # Context gathering (read tools only, allow-list enforced)
    # ------------------------------------------------------------------

    def _context(self, workflow: str, tool_context: ToolContext) -> WorkflowContext:
        allowed = self.registry.allowed_read_tools(workflow)
        return WorkflowContext(
            tool_context=tool_context,
            workflow=workflow,
            allowed_tools=allowed,
            budget=tool_context.budget,
        )

    def _run_tool(self, context: WorkflowContext, name: str, **kwargs: Any) -> Any:
        if name not in context.allowed_tools:
            raise InvalidActionOutput(
                f"workflow tool {name} is not allow-listed",
                meta={"workflow": context.workflow, "tool": name},
            )
        return self.registry.invoke(
            context.workflow, name, context.tool_context, **kwargs
        )

    def _gather_files(
        self,
        context: WorkflowContext,
        scope: JobScope,
    ) -> tuple[list[str], set[str]]:
        """Return (context files, every markdown path in scope/vault)."""
        all_markdown = {
            path
            for path in self._run_tool(context, "vault.list")  # type: ignore[arg-type]
            if path.casefold().endswith(MARKDOWN_SUFFIXES)
        }
        if scope.paths:
            files = [path for path in scope.paths if path in all_markdown]
        else:
            files = sorted(all_markdown)
        return files[: scope.max_files], all_markdown

    async def plan(
        self,
        *,
        task: TaskType,
        request: JobRequest,
        adapter: ModelAdapter,
        tool_context: ToolContext,
        model: str,
        temperature: float = 0.1,
        timeout_seconds: float = 2.0,
        max_output_tokens: int = 1200,
    ) -> ActionSet:
        """Gather a bounded context and call the model exactly once."""
        meta = WORKFLOWS.get(task)
        if meta is None:
            raise InvalidActionOutput(f"unknown workflow {task}")
        context = self._context(task, tool_context)
        files, all_markdown = self._gather_files(context, request.scope)
        notes_payload: list[dict[str, Any]] = []
        total_chars = 0
        per_file_cap = request.scope.max_chars // max(1, request.scope.max_files)
        for path in files:
            try:
                read = self._run_tool(
                    context, "vault.read", path=path, max_chars=per_file_cap
                )
                tags: list[str] = []
                try:
                    tags = list(self._run_tool(context, "metadata.get", path=path).get("tags", []))
                except Exception:
                    tags = []
                excerpt = str(read.get("content", ""))
                total_chars += len(excerpt)
                notes_payload.append({"path": path, "tags": tags[:10], "content": excerpt})
            except Exception:
                continue
            if total_chars >= request.scope.max_chars:
                break
        allow_files = set(files)
        user_prompt = (
            f"{meta.description}\n\n"
            f"Scope files (only these may be changed): "
            f"{json.dumps(sorted(allow_files), ensure_ascii=False)}\n"
            f"Notes in the vault (link targets may be chosen from here): "
            f"{json.dumps(sorted(all_markdown), ensure_ascii=False)}\n\n"
            f"Context:\n{json.dumps(notes_payload, ensure_ascii=False)[:request.scope.max_chars]}"
        )
        schema = self._action_schema()
        prompt_version = f"{task}@{PROMPT_VERSION_PREFIX}"
        try:
            result = await adapter.chat(
                model=model,
                messages=[
                    ChatMessage("system", _SYSTEM_PROMPT),
                    ChatMessage("user", user_prompt),
                ],
                temperature=temperature,
                response_schema=schema,
                timeout_seconds=timeout_seconds,
                max_output_tokens=max_output_tokens,
            )
        except AIAdapterError as exc:
            raise AIUnavailable(str(exc)) from exc
        except AIError as exc:
            raise AIUnavailable(exc.message) from exc
        actions = self._parse(result.content, prompt_version, result.model, task)
        self._enforce_scope(
            actions,
            allow_files,
            all_markdown,
            task,
            meta,
            scope_paths=request.scope.paths,
        )
        return actions

    @staticmethod
    def _action_schema() -> dict[str, object]:
        schema = ActionSet.model_json_schema()
        properties = schema.get("properties")
        if isinstance(properties, dict):
            properties.pop("model", None)
            properties.pop("prompt_version", None)
        required = schema.get("required")
        if isinstance(required, list) and properties:
            schema["required"] = [
                name for name in required if name in properties
            ]
        return schema

    @staticmethod
    def _parse(raw: str, prompt_version: str, model: str, task: TaskType) -> ActionSet:
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        try:
            payload = json.loads(text)
        except Exception as exc:
            raise InvalidActionOutput(
                "Model did not return a JSON ActionSet",
                meta={"prompt_version": prompt_version, "model": model},
            ) from exc
        try:
            actions = parse_action_set(payload)
        except Exception as exc:
            raise InvalidActionOutput(
                f"Model returned an invalid ActionSet: {exc}",
                meta={"prompt_version": prompt_version, "model": model},
            ) from exc
        validated = validate_action_set(actions)
        validated = validated.model_copy(
            update={
                "task_type": task,
                "model": model,
                "prompt_version": prompt_version,
            }
        )
        return validated

    @staticmethod
    def _enforce_scope(
        actions: ActionSet,
        allow_files: set[str],
        all_markdown: set[str],
        task: str,
        meta: WorkflowMeta,
        scope_paths: list[str] = (),
    ) -> None:
        """Validate every proposed action against the workflow + scope rules.

        ``allow_files`` are the *existing* files the workflow may touch.
        A ``create_note`` targets a brand-new path, so it can never be a
        member of ``allow_files``; instead it must be a safe new Markdown
        path inside the request scope directory and must not collide with an
        existing note (see :func:`create_target_problem`).
        """
        allowed_types = {action_type.value for action_type in meta.allowed_actions}
        for action in actions.actions:
            if action.action.value not in allowed_types:
                raise InvalidActionOutput(
                    f"action {action.action.value} is not allowed by {task}",
                    meta={"action": action.action.value, "workflow": task},
                )
            if action.action == ActionType.CREATE_NOTE:
                problem = create_target_problem(
                    action.file,
                    scope_paths=scope_paths,
                    existing_markdown=all_markdown,
                )
                if problem is not None:
                    raise InvalidActionOutput(
                        f"{problem}: {action.file}",
                        meta={"path": action.file, "workflow": task},
                    )
            elif action.file not in allow_files:
                raise InvalidActionOutput(
                    f"path {action.file} is outside the workflow scope",
                    meta={"path": action.file, "workflow": task},
                )
            if action.link_target and action.link_target not in all_markdown:
                raise InvalidActionOutput(
                    f"link target {action.link_target} is not an existing note",
                    meta={"path": action.link_target, "workflow": task},
                )
            for target in (action.target_file,):
                if target and target in all_markdown:
                    raise InvalidActionOutput(
                        f"target {target} already exists; overwriting is denied",
                        meta={"path": target, "workflow": task},
                    )


__all__ = ["WORKFLOWS", "WorkflowContext", "WorkflowMeta", "WorkflowPlanner"]
