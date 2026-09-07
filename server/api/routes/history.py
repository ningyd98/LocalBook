"""History REST API for M7 (PLAN-M7 §6.1, M7-10).

Endpoints:

- ``GET /api/v1/history`` — paginated History (optional status filter);
- ``GET /api/v1/history/{id}`` — detail incl. diff/journal projection;
- ``POST /api/v1/history/{id}/undo`` — model-free, hash-guarded restore.

Same error contract as jobs: typed errors, central handler, no ``HTTPException``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from server.agents.schemas import UndoJobRequest
from server.agents.service import AgentJobService
from server.api.dependencies import get_agent_job_service

router = APIRouter(prefix="/api/v1", tags=["history"])

JobServiceDep = Annotated[AgentJobService, Depends(get_agent_job_service)]


@router.get("/history")
async def list_history(
    service: JobServiceDep,
    limit: int = 20,
    offset: int = 0,
    status: str | None = None,
):
    return service.list_jobs(limit=limit, offset=offset, status=status)


@router.get("/history/{job_id}")
async def history_detail(job_id: str, service: JobServiceDep):
    return service.get_job(job_id)


@router.post("/history/{job_id}/undo")
async def undo_job(job_id: str, body: UndoJobRequest, service: JobServiceDep):
    return service.undo(job_id, body)
