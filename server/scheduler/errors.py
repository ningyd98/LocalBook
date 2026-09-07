"""Stable M8 scheduler error codes (PLAN-M8 §6.2).

Every error carries a fixed public code, a fixed safe message and optional
``meta`` (run_id/task).  Absolute roots, prompt text, request bodies, tokens
and stack traces never reach a message.  ``network_exposure_warning`` is a
status *flag*, never a thrown error.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class SchedulerErrorCode(StrEnum):
    SCHEDULER_DISABLED = "scheduler_disabled"
    SCHEDULER_UNAVAILABLE = "scheduler_unavailable"
    UNKNOWN_TASK = "unknown_task"
    INVALID_SCHEDULE = "invalid_schedule"
    DUPLICATE_RUN = "duplicate_run"
    JOB_TIMEOUT = "job_timeout"
    JOB_IN_PROGRESS = "job_in_progress"
    RECOVERY_REQUIRED = "recovery_required"
    RECOVERY_NOT_SAFE = "recovery_not_safe"
    SCHEDULER_CONFIG_INVALID = "scheduler_config_invalid"
    HISTORY_CLEANUP_FAILED = "history_cleanup_failed"
    INDEX_CHECK_FAILED = "index_check_failed"
    # Status-only advisory; never raised.
    NETWORK_EXPOSURE_WARNING = "network_exposure_warning"


class SchedulerError(Exception):
    """Base for expected scheduler failures; safe to serialize."""

    code: SchedulerErrorCode = SchedulerErrorCode.SCHEDULER_UNAVAILABLE
    default_status = 503
    default_message = "Scheduler operation failed"

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


class SchedulerDisabled(SchedulerError):
    code = SchedulerErrorCode.SCHEDULER_DISABLED
    default_status = 409
    default_message = "Scheduler is disabled"


class SchedulerUnavailable(SchedulerError):
    code = SchedulerErrorCode.SCHEDULER_UNAVAILABLE
    default_status = 503
    default_message = "Scheduler is unavailable"


class UnknownTask(SchedulerError):
    code = SchedulerErrorCode.UNKNOWN_TASK
    default_status = 400
    default_message = "Unknown scheduler task"


class InvalidSchedule(SchedulerError):
    code = SchedulerErrorCode.INVALID_SCHEDULE
    default_status = 400
    default_message = "Invalid schedule"


class DuplicateRun(SchedulerError):
    code = SchedulerErrorCode.DUPLICATE_RUN
    default_status = 409
    default_message = "A run for this slot already exists"


class JobTimeout(SchedulerError):
    code = SchedulerErrorCode.JOB_TIMEOUT
    default_status = 504
    default_message = "Scheduler job timed out"


class JobInProgress(SchedulerError):
    code = SchedulerErrorCode.JOB_IN_PROGRESS
    default_status = 409
    default_message = "The task already has an active run"


class RecoveryRequired(SchedulerError):
    code = SchedulerErrorCode.RECOVERY_REQUIRED
    default_status = 409
    default_message = "Recovery is required before this operation"


class RecoveryNotSafe(SchedulerError):
    code = SchedulerErrorCode.RECOVERY_NOT_SAFE
    default_status = 409
    default_message = "Recovery is not safe (files changed externally or state is not uncertain)"


class SchedulerConfigInvalid(SchedulerError):
    code = SchedulerErrorCode.SCHEDULER_CONFIG_INVALID
    default_status = 400
    default_message = "Scheduler configuration is invalid"


class HistoryCleanupFailed(SchedulerError):
    code = SchedulerErrorCode.HISTORY_CLEANUP_FAILED
    default_status = 503
    default_message = "History cleanup failed"


class IndexCheckFailed(SchedulerError):
    code = SchedulerErrorCode.INDEX_CHECK_FAILED
    default_status = 503
    default_message = "Index consistency check failed"


__all__ = [
    "DuplicateRun",
    "HistoryCleanupFailed",
    "IndexCheckFailed",
    "InvalidSchedule",
    "JobInProgress",
    "JobTimeout",
    "RecoveryNotSafe",
    "RecoveryRequired",
    "SchedulerConfigInvalid",
    "SchedulerDisabled",
    "SchedulerError",
    "SchedulerErrorCode",
    "SchedulerUnavailable",
    "UnknownTask",
]
