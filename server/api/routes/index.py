"""REST endpoint for M3 manual index rebuild (PLAN-M3 §5.7/§6.1).

``POST /api/v1/index/rebuild`` triggers a full in-memory rescan through the
VaultService and returns a summary — not an empty 204 — so clients can
confirm recovery.  It never touches ``.localnote`` and never writes files.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from ...index.schemas import IndexRebuildResponse
from ...index.service import DerivedIndexService
from ..dependencies import get_index_service

router = APIRouter(prefix="/api/v1/index", tags=["index"])
IndexServiceDep = Annotated[DerivedIndexService, Depends(get_index_service)]


@router.post("/rebuild", response_model=IndexRebuildResponse)
def rebuild_index(index: IndexServiceDep) -> IndexRebuildResponse:
    return index.rebuild()


__all__ = ["router"]
