"""M8 scheduler package: schedule/dispatch only, no business logic.

Public exports mirror the files listed in PLAN-M8 §7.  Business behaviour
stays in the M7 chain (AgentJobService/Policy/History/Recovery) and Vault
writes stay behind ``VaultService``; this package only decides *when* a
trigger runs and records an auditable run row.
"""

from .clock import Clock, SystemClock, iso_utc
from .errors import (
    DuplicateRun,
    HistoryCleanupFailed,
    IndexCheckFailed,
    InvalidSchedule,
    JobInProgress,
    JobTimeout,
    RecoveryNotSafe,
    RecoveryRequired,
    SchedulerConfigInvalid,
    SchedulerDisabled,
    SchedulerError,
    SchedulerErrorCode,
    SchedulerUnavailable,
    UnknownTask,
)
from .models import (
    JobDefinition,
    RunHandle,
    SchedulerRun,
    SchedulerRunStatus,
    to_row,
)
from .service import SchedulerService
from .service_types import (
    RecoveryActionRequest,
    SchedulerRunRequest,
    SchedulerRunResponse,
)

__all__ = [
    "Clock",
    "DuplicateRun",
    "HistoryCleanupFailed",
    "IndexCheckFailed",
    "InvalidSchedule",
    "JobDefinition",
    "JobInProgress",
    "JobTimeout",
    "RecoveryActionRequest",
    "RecoveryNotSafe",
    "RecoveryRequired",
    "RunHandle",
    "SchedulerConfigInvalid",
    "SchedulerDisabled",
    "SchedulerError",
    "SchedulerErrorCode",
    "SchedulerRun",
    "SchedulerRunRequest",
    "SchedulerRunResponse",
    "SchedulerRunStatus",
    "SchedulerService",
    "SchedulerUnavailable",
    "SystemClock",
    "UnknownTask",
    "iso_utc",
    "to_row",
]
