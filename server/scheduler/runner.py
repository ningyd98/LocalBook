"""M7-scheduled JobRunner for M8 (PLAN-M8 §3.1/§5.3).

The runner answers "which controlled business chain runs when a trigger
fires": for Daily/Weekly it builds a bounded M7 ``JobRequest`` and calls the
existing ``AgentJobService.plan`` (public controlled entry, never copied
execution code).  Default is Level 1 preview; a scheduled run may only
auto-execute when all of the following hold — server Level-2 switch on, a
non-empty tag-only whitelist, the returned action set ⊆ that whitelist, one
file, no body rewrite, and the M7 PolicyEngine itself returning ``allow`` —
in which case the existing public ``accept`` entry is used.  Anything else
stops at preview/failed; the runner never downgrades a confirm/deny into an
allow.  ``index_consistency`` dispatches to the read-only checker instead of
the agent chain.  The runner has no prompt, action, URL, shell or policy
inputs of its own.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from server.actions.schemas import ActionType
from server.agents.schemas import AcceptJobRequest, JobRequest, JobScope
from server.agents.service import AgentJobService
from server.policies.errors import M7Error

from .index_job import IndexConsistencyChecker, to_message
from .models import TASK_IDS, SchedulerRunStatus

logger = logging.getLogger("localnote.scheduler")

_LEVEL2_TAG_ONLY = {ActionType.ADD_TAGS, ActionType.REMOVE_TAGS}

DEFAULT_MAX_FILES = 10
DEFAULT_MAX_CHARS = 60_000


@dataclass
class RunOutcome:
    """Result of one handler execution (persisted by the scheduler service)."""

    status: SchedulerRunStatus
    agent_job_id: str | None = None
    policy: dict[str, Any] | None = None
    error_code: str | None = None
    message: str | None = None
    detail: dict[str, Any] | None = None


def _message_of(exc: Exception) -> str:
    return str(exc).strip()[:500] or exc.__class__.__name__


def _policy_view(detail: dict[str, Any] | None) -> dict[str, Any] | None:
    policy = detail.get("policy") if detail else None
    return policy if isinstance(policy, dict) else None


class JobRunner:
    """Trigger → controlled M7 job chain (or index check) adapter."""

    def __init__(
        self,
        agent_service: AgentJobService | None = None,
        *,
        index_checker: IndexConsistencyChecker | None = None,
        level2_auto_enabled: bool = False,
        level2_auto_actions: set[ActionType] | frozenset[ActionType] = frozenset(),
        default_scope: JobScope | None = None,
    ) -> None:
        self.agent_service = agent_service
        self.index_checker = index_checker
        self.level2_auto_enabled = bool(level2_auto_enabled)
        self.level2_auto_actions = frozenset(level2_auto_actions) & _LEVEL2_TAG_ONLY
        self.default_scope = default_scope or JobScope(
            paths=[], max_files=DEFAULT_MAX_FILES, max_chars=DEFAULT_MAX_CHARS
        )

    # ------------------------------------------------------------------
    # Entry points
    # ------------------------------------------------------------------

    async def arun(
        self,
        task: TASK_IDS,
        *,
        scope: dict[str, Any] | None = None,
        request_auto_level2: bool | None = None,
    ) -> RunOutcome:
        """Run one task; mirrors the manual chain for every trigger kind.

        ``request_auto_level2=None`` means "follow the server configuration"
        (scheduled/startup triggers); an explicit bool is a manual request
        that can only *narrow* the configured switch.
        """
        if task == "index_consistency":
            return self._run_index_check()
        if task not in ("daily_organizer", "weekly_review"):
            return RunOutcome(
                status=SchedulerRunStatus.FAILED,
                error_code="unknown_task",
                message=f"unknown scheduler task: {task}",
            )
        return await self._run_agent_task(
            task, scope=scope, request_auto_level2=request_auto_level2
        )

    # ------------------------------------------------------------------
    # Daily / Weekly through the M7 chain
    # ------------------------------------------------------------------

    async def _run_agent_task(
        self,
        task: TASK_IDS,
        *,
        scope: dict[str, Any] | None,
        request_auto_level2: bool | None,
    ) -> RunOutcome:
        if self.agent_service is None:
            return RunOutcome(
                status=SchedulerRunStatus.FAILED,
                error_code="scheduler_unavailable",
                message="agent job service is unavailable",
            )
        effective_scope = self._effective_scope(scope)
        # A manual ``auto_level2`` request can never open the server switch;
        # it only narrows an already-enabled Level 2.  Scheduled triggers
        # follow the configuration directly.  A switch with an *empty*
        # whitelist is an inconsistent configuration (settings validation
        # rejects it); the runner degrades to Level 1 instead of guessing.
        wants_level2 = True if request_auto_level2 is None else bool(request_auto_level2)
        level2 = bool(self.level2_auto_enabled and self.level2_auto_actions and wants_level2)
        level = 2 if level2 else 1
        request = JobRequest(
            task_type=task,
            permission_level=level,
            scope=effective_scope,
            actions=[],
            execute=False,
        )
        try:
            detail = await self.agent_service.plan(request)
        except M7Error as exc:
            rules = exc.meta.get("rules")
            policy = (
                {"decision": "deny", "matched_rules": list(rules or []), "reasons": []}
                if rules
                else None
            )
            return RunOutcome(
                status=SchedulerRunStatus.FAILED,
                agent_job_id=exc.meta.get("job_id"),
                policy=policy,
                error_code=str(exc.code.value) if hasattr(exc, "code") else "m7_error",
                message=_message_of(exc),
            )
        except Exception:  # noqa: BLE001 - bounded safe conversion (S3)
            logger.exception("unexpected error while planning task=%s", task)
            return RunOutcome(
                status=SchedulerRunStatus.FAILED,
                error_code="scheduler_unavailable",
                message="unexpected error during agent planning (details logged)",
            )
        policy = _policy_view(detail)
        decision = policy.get("decision") if policy else None
        if level == 1:
            # Level 1 always stops at preview/awaiting_confirmation.
            return RunOutcome(
                status=SchedulerRunStatus.PREVIEWED,
                agent_job_id=str(detail.get("job_id")),
                policy=policy,
                detail=detail,
            )
        if decision != "allow":
            return RunOutcome(
                status=SchedulerRunStatus.FAILED,
                agent_job_id=str(detail.get("job_id")),
                policy=policy,
                error_code="policy_denied",
                message="policy did not allow automatic execution",
            )
        if not self._whitelist_ok(detail):
            return RunOutcome(
                status=SchedulerRunStatus.FAILED,
                agent_job_id=str(detail.get("job_id")),
                policy=policy,
                error_code="policy_denied",
                message="actions are outside the server Level-2 tag-only whitelist",
            )
        # Policy allow + whitelist: execute through the existing accept path
        # (re-preflight → journal → VaultService → History).
        job_id = str(detail.get("job_id"))
        try:
            committed = self.agent_service.accept(
                job_id, AcceptJobRequest(confirm=True, action_ids=[])
            )
        except M7Error as exc:
            return RunOutcome(
                status=SchedulerRunStatus.FAILED,
                agent_job_id=job_id,
                policy=policy,
                error_code=str(exc.code.value) if hasattr(exc, "code") else "m7_error",
                message=_message_of(exc),
            )
        except Exception:  # noqa: BLE001 - bounded safe conversion (S3)
            logger.exception("unexpected error during accept task=%s job=%s", task, job_id)
            return RunOutcome(
                status=SchedulerRunStatus.FAILED,
                agent_job_id=job_id,
                policy=policy,
                error_code="transaction_failed",
                message="unexpected error during execution (details logged)",
            )
        return RunOutcome(
            status=SchedulerRunStatus.COMMITTED,
            agent_job_id=job_id,
            policy=policy,
            detail=committed,
            message="automatic Level-2 execution committed",
        )

    def _whitelist_ok(self, detail: dict[str, Any]) -> bool:
        if not self.level2_auto_actions:
            return False
        actions = detail.get("proposed_actions") or []
        try:
            kinds = {str(action.get("action", "")) for action in actions}
        except Exception:  # pragma: no cover - defensive
            return False
        allowed = {action.value for action in self.level2_auto_actions}
        return bool(kinds) and kinds <= allowed

    def _effective_scope(self, scope: dict[str, Any] | None) -> JobScope:
        if not scope:
            return self.default_scope
        try:
            return JobScope.model_validate(scope)
        except Exception:  # noqa: BLE001 - server clamps to defaults on bad input
            return self.default_scope

    # ------------------------------------------------------------------
    # index_consistency
    # ------------------------------------------------------------------

    def _run_index_check(self) -> RunOutcome:
        if self.index_checker is None:
            return RunOutcome(
                status=SchedulerRunStatus.FAILED,
                error_code="index_check_failed",
                message="index consistency job is not configured",
            )
        try:
            result = self.index_checker.check()
        except Exception:  # noqa: BLE001 - bounded safe conversion (S3)
            logger.exception("unexpected error during index consistency check")
            return RunOutcome(
                status=SchedulerRunStatus.FAILED,
                error_code="index_check_failed",
                message="index consistency check failed unexpectedly (details logged)",
            )
        if result.degraded:
            return RunOutcome(
                status=SchedulerRunStatus.FAILED,
                error_code="index_check_failed",
                message=to_message(result),
            )
        message = to_message(result)
        if result.mismatches and not result.rebuilt:
            return RunOutcome(
                status=SchedulerRunStatus.FAILED,
                error_code="index_check_failed",
                message=message,
            )
        return RunOutcome(
            status=SchedulerRunStatus.COMMITTED,
            message=message,
            detail={
                "checked": result.checked,
                "mismatches": result.mismatches,
                "rebuilt": result.rebuilt,
                "duration_ms": result.duration_ms,
            },
        )


__all__ = ["JobRunner", "RunOutcome"]
