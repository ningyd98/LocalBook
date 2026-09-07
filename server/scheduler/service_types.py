"""Wire DTOs for the M8 scheduler API (PLAN-M8 §6.1)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class SchedulerRunRequest(BaseModel):
    """Manual trigger body — bounded and clamped server-side.

    ``confirm`` participates in the decision (S2): an unconfirmed manual
    request (``confirm`` false/missing) can never auto-execute — it always
    produces the Level-1 preview even when the server Level-2 tag-only switch
    is on.  ``confirm=true`` alone never grants a write: Level-2 automatic
    execution additionally requires ``auto_level2=true`` *and* the server-side
    switch + whitelist + Policy ``allow``; a write is only ever committed
    through the existing M7 ``POST /api/v1/jobs/{id}/accept`` endpoint.
    ``auto_level2`` cannot open the server-side switch by itself; it can only
    narrow an already-enabled Level-2 tag-only policy.
    """

    model_config = ConfigDict(extra="forbid")

    confirm: bool = False
    auto_level2: bool = False
    scope: dict[str, object] | None = None


class SchedulerRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    task: str
    status: str
    trigger: str
    agent_job_id: str | None = None
    scheduled_for: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    policy: dict[str, object] | None = None
    error_code: str | None = None
    message: str | None = None
    detail: dict[str, object] | None = None


class RecoveryActionRequest(BaseModel):
    """Explicit recovery body; defaults to read-only ``diagnose``."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["diagnose", "rollback_if_safe", "retry_preview"] = "diagnose"


__all__ = ["RecoveryActionRequest", "SchedulerRunRequest", "SchedulerRunResponse"]
