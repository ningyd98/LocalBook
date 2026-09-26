"""Read-only browser previews for common Vault document attachments."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from ...documents.errors import DocumentPreviewError
from ...documents.service import DocumentPreviewArtifact, DocumentPreviewService
from ..dependencies import get_document_preview_service

router = APIRouter(prefix="/api/v1/vault", tags=["documents"])
DocumentPreviewServiceDep = Annotated[
    DocumentPreviewService,
    Depends(get_document_preview_service),
]


@router.get("/document-preview")
def preview_vault_document(
    service: DocumentPreviewServiceDep,
    path: str = Query(...),
) -> StreamingResponse:
    """Return a browser-readable PDF for a PDF or Office attachment."""
    artifact: DocumentPreviewArtifact | None = None
    try:
        artifact = service.open_preview(path)
        return StreamingResponse(
            artifact.stream(),
            media_type="application/pdf",
            headers={
                "Content-Length": str(artifact.content_length),
                "Content-Disposition": "inline",
                "Cache-Control": "no-cache",
                "X-Content-Type-Options": "nosniff",
            },
            background=BackgroundTask(artifact.close),
        )
    except DocumentPreviewError:
        if artifact is not None:
            artifact.close()
        raise


__all__ = ["router"]
