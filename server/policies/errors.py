"""Stable M7 domain errors shared by policy/history/recovery/agents/API.

Every error carries a fixed public code, a fixed safe message, an optional
relative path and extra ``meta`` (e.g. ``job_id``).  Absolute roots, request
bodies, tokens and stack traces are never part of a message.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class M7ErrorCode(StrEnum):
    POLICY_DENIED = "policy_denied"
    CONFIRMATION_REQUIRED = "confirmation_required"
    INVALID_ACTION = "invalid_action"
    INVALID_ACTION_OUTPUT = "invalid_action_output"
    UNSUPPORTED_ACTION = "unsupported_action"
    JOB_NOT_FOUND = "job_not_found"
    JOB_STATE_CONFLICT = "job_state_conflict"
    TRANSACTION_FAILED = "transaction_failed"
    ROLLBACK_FAILED = "rollback_failed"
    UNDO_CONFLICT = "undo_conflict"
    UNDO_UNAVAILABLE = "undo_unavailable"
    HISTORY_UNAVAILABLE = "history_unavailable"
    HISTORY_LIMIT_EXCEEDED = "history_limit_exceeded"
    AI_UNAVAILABLE = "ai_unavailable"


class M7Error(Exception):
    """Base for all expected M7 failures; safe to serialize to API clients."""

    code: M7ErrorCode = M7ErrorCode.TRANSACTION_FAILED
    default_status = 500
    default_message = "M7 operation failed"

    def __init__(
        self,
        message: str | None = None,
        *,
        path: str | None = None,
        status_code: int | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.default_message
        self.path = path
        self.status_code = status_code or self.default_status
        self.meta: dict[str, Any] = meta or {}
        super().__init__(self.message)


class PolicyDenied(M7Error):
    code = M7ErrorCode.POLICY_DENIED
    default_status = 403
    default_message = "Policy denied the action set"


class ConfirmationRequired(M7Error):
    code = M7ErrorCode.CONFIRMATION_REQUIRED
    default_status = 409
    default_message = "Explicit confirmation is required"


class InvalidAction(M7Error):
    code = M7ErrorCode.INVALID_ACTION
    default_status = 400
    default_message = "Invalid action"


class InvalidActionOutput(M7Error):
    code = M7ErrorCode.INVALID_ACTION_OUTPUT
    default_status = 502
    default_message = "Model returned invalid structured output"


class UnsupportedAction(M7Error):
    code = M7ErrorCode.UNSUPPORTED_ACTION
    default_status = 400
    default_message = "Unsupported action"


class JobNotFound(M7Error):
    code = M7ErrorCode.JOB_NOT_FOUND
    default_status = 404
    default_message = "Job not found"


class JobStateConflict(M7Error):
    code = M7ErrorCode.JOB_STATE_CONFLICT
    default_status = 409
    default_message = "Job state conflict"


class TransactionFailed(M7Error):
    code = M7ErrorCode.TRANSACTION_FAILED
    default_status = 500
    default_message = "Transaction failed"


class RollbackFailed(M7Error):
    code = M7ErrorCode.ROLLBACK_FAILED
    default_status = 500
    default_message = "Rollback failed; manual review required"


class UndoConflict(M7Error):
    code = M7ErrorCode.UNDO_CONFLICT
    default_status = 409
    default_message = "Files changed since the job ran; refusing to overwrite"


class UndoUnavailable(M7Error):
    code = M7ErrorCode.UNDO_UNAVAILABLE
    default_status = 409
    default_message = "Undo is unavailable for this job"


class HistoryUnavailable(M7Error):
    code = M7ErrorCode.HISTORY_UNAVAILABLE
    default_status = 503
    default_message = "History is unavailable"


class HistoryLimitExceeded(M7Error):
    code = M7ErrorCode.HISTORY_LIMIT_EXCEEDED
    default_status = 413
    default_message = "History payload exceeds the size limit"


class AIUnavailable(M7Error):
    code = M7ErrorCode.AI_UNAVAILABLE
    default_status = 503
    default_message = "AI planner is unavailable"


__all__ = [
    "AIUnavailable",
    "ConfirmationRequired",
    "HistoryLimitExceeded",
    "HistoryUnavailable",
    "InvalidAction",
    "InvalidActionOutput",
    "JobNotFound",
    "JobStateConflict",
    "M7Error",
    "M7ErrorCode",
    "PolicyDenied",
    "RollbackFailed",
    "TransactionFailed",
    "UndoConflict",
    "UndoUnavailable",
    "UnsupportedAction",
]
