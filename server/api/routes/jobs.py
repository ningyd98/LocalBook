"""Job REST API for M7 (PLAN-M7 §6.1, M7-10).

Endpoints:

- ``POST /api/v1/jobs`` — controlled plan (preview by default, no writes);
- ``GET /api/v1/jobs`` — paginated job list;
- ``POST /api/v1/jobs/{id}/accept`` — confirm + execute (Level 1) or run a
  Level-2 allowed job; server re-preflights before any write;
- ``POST /api/v1/jobs/{id}/reject`` — reject the diff without writing.

Domain failures are raised as typed errors and rendered by the central
``{"error": {...}, "meta": {...}}`` handler in ``main.py``; no route raises
``HTTPException`` and no absolute path/body/stack can leak.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from server.agents.schemas import AcceptJobRequest, JobRequest
from server.agents.service import AgentJobService
from server.api.dependencies import get_agent_job_service

router = APIRouter(prefix="/api/v1", tags=["jobs"])

JobServiceDep = Annotated[AgentJobService, Depends(get_agent_job_service)]


@router.post("/jobs")
async def create_job(request: JobRequest, service: JobServiceDep):
    """Plan a job: validate, policy-check and produce a diff — never writes."""
    return await service.plan(request)


@router.get("/jobs")
async def list_jobs(
    service: JobServiceDep,
    limit: int = 20,
    offset: int = 0,
    status: str | None = None,
):
    return service.list_jobs(limit=limit, offset=offset, status=status)


@router.post("/jobs/{job_id}/accept")
async def accept_job(
    job_id: str,
    body: AcceptJobRequest,
    service: JobServiceDep,
):
    """Confirm + execute the accepted actions (re-preflighted server-side)."""
    return service.accept(job_id, body)


@router.post("/jobs/{job_id}/reject")
async def reject_job(job_id: str, service: JobServiceDep):
    return service.reject(job_id)
