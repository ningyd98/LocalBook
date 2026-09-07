"""REST endpoint for M3 keyword search (PLAN-M3 §5.5/§5.7).

``GET /api/v1/search?q=...`` — substring, case-insensitive, AND over terms.
Empty/control/over-long queries map to 400 ``invalid_request``; an unavailable
index maps to 503 ``index_unavailable``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from ...search.schemas import SearchResponse
from ...search.service import SearchService
from ..dependencies import get_search_service

router = APIRouter(prefix="/api/v1/search", tags=["search"])
SearchServiceDep = Annotated[SearchService, Depends(get_search_service)]


@router.get("", response_model=SearchResponse)
def search_notes(
    service: SearchServiceDep,
    q: str = Query(..., description="Keyword query; terms are ANDed"),
) -> SearchResponse:
    return service.search(q)


__all__ = ["router"]
