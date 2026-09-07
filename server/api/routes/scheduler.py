"""Scheduler REST API for M8 (PLAN-M8 §6.1, M8-10).

Endpoints:
- ``GET  /api/v1/scheduler/status`` — enabled/running/degraded + per-job
  next run/last status, recovery count, LAN warning;
- ``POST /api/v1/scheduler/run/{task}`` — manual trigger of a *stable* task
  (``daily_organizer`` | ``weekly_review`` | ``index_consistency``); the same
  handler as scheduled triggers, always through M7 Policy/History;
- ``GET  /api/v1/scheduler/runs`` — paginated run records;
- ``POST /api/v1/scheduler/recovery/{run_id}`` — explicit recovery
  (default ``diagnose``; rollback is hash-guarded).

Errors keep the ``{"error": {...}, "meta": {...}}`` contract via the central
SchedulerError handler; no route raises ``HTTPException`` and no absolute
root/body/stack can leak.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from server.api.dependencies import get_scheduler_service
from server.scheduler.service_types import RecoveryActionRequest, SchedulerRunRequest

router = APIRouter(prefix="/api/v1/scheduler", tags=["scheduler"])

SchedulerServiceDep = Annotated[object, Depends(get_scheduler_service)]


@router.get("/status")
def scheduler_status(service: SchedulerServiceDep):
    return service.status()


@router.post("/run/{task}")
async def scheduler_run_task(
    task: str,
    body: SchedulerRunRequest,
    service: SchedulerServiceDep,
):
    """Manually trigger one stable task (preview by default; no client write)."""
    return await service.arun_task(task, body.model_dump())


@router.get("/runs")
def scheduler_runs(
    service: SchedulerServiceDep,
    limit: int = 20,
    offset: int = 0,
    task: str | None = None,
):
    return service.list_runs(limit=limit, offset=offset, task=task)


@router.post("/recovery/{run_id}")
def scheduler_recovery(
    run_id: str,
    body: RecoveryActionRequest,
    service: SchedulerServiceDep,
):
    """Explicit recovery action for one flagged run (diagnose by default)."""
    return service.recovery_action(run_id, body.action)


__all__ = ["router"]
