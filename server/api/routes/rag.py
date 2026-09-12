"""M14 RAG REST endpoints (read-only, derived data only).

- ``POST /api/v1/rag/search``      retrieval only (never calls a chat model);
- ``POST /api/v1/rag/query``       retrieve → rerank → grounded answer;
- ``POST /api/v1/rag/index/rebuild`` rebuild the derived RAG index;
- ``GET  /api/v1/rag/index/status`` index state, counters and degradation.

Nothing in this router writes to the Vault: the only writes are derived rows in
``.localnote/index.db``, which are always rebuildable from the Markdown files.
A missing/unavailable RAG stack returns 503 with a stable error code instead of
breaking the rest of the API.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends

from ...rag.api_schemas import (
    RagIndexRebuildResponse,
    RagIndexStatusResponse,
    RagQueryRequest,
    RagQueryResponse,
    RagSearchRequest,
    RagSearchResponse,
)
from ...rag.service import RagService
from ..dependencies import get_rag_service

logger = logging.getLogger("localnote.api.rag")

router = APIRouter(prefix="/api/v1/rag", tags=["rag"])
RagDep = Annotated[RagService, Depends(get_rag_service)]


@router.get("/index/status", response_model=RagIndexStatusResponse)
def rag_index_status(service: RagDep) -> RagIndexStatusResponse:
    return service.status()


@router.post("/index/rebuild", response_model=RagIndexRebuildResponse)
def rag_index_rebuild(service: RagDep) -> RagIndexRebuildResponse:
    started = time.perf_counter()
    result = service._index.rebuild()  # noqa: SLF001 - router is the thin shell
    status = service.status()
    _ = (time.perf_counter() - started) * 1000.0
    logger.info(
        "rag index rebuild documents=%d chunks=%d failed=%d ready=%s",
        result.indexed_documents,
        result.indexed_chunks,
        result.failed_documents,
        result.ready,
    )
    return RagIndexRebuildResponse(
        indexed_documents=result.indexed_documents,
        indexed_chunks=result.indexed_chunks,
        embedded_chunks=result.embedded_chunks,
        skipped_documents=result.skipped_documents,
        failed_documents=result.failed_documents,
        duration_ms=result.duration_ms,
        ready=result.ready,
        degraded=result.embedding_degraded,
        degraded_reason=result.degraded_reason,
        status=status,
    )


@router.post("/search", response_model=RagSearchResponse)
def rag_search(request: RagSearchRequest, service: RagDep) -> RagSearchResponse:
    return service.search(
        request.query, top_k=request.top_k, rerank=request.rerank
    )


@router.post("/query", response_model=RagQueryResponse)
async def rag_query(request: RagQueryRequest, service: RagDep) -> RagQueryResponse:
    response = await service.query(
        request.query,
        top_k=request.top_k,
        rerank=request.rerank,
        include_debug=request.debug,
    )
    if response.generated_at is None:  # pragma: no cover - defensive
        return response.model_copy(update={"generated_at": datetime.now(UTC)})
    return response


__all__ = ["router"]
