"""REST endpoints for M3 frontmatter/Metadata (PLAN-M3 §5.7/§6.1).

Both URL forms are served: ``/metadata/{note:path}`` (path converter, the
primary contract — frontends encode each path segment) and ``/metadata?path=``
(the M1-style query form).  Parse failures are HTTP 200 with structured
``parse_error``; missing notes 404; vault/index unavailability 503.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from ...metadata.schemas import NoteMetadataResponse
from ...metadata.service import MetadataService
from ..dependencies import get_metadata_service

router = APIRouter(prefix="/api/v1/metadata", tags=["metadata"])
MetadataServiceDep = Annotated[MetadataService, Depends(get_metadata_service)]


@router.get("/{note:path}", response_model=NoteMetadataResponse)
def get_note_metadata_by_path(
    note: str,
    service: MetadataServiceDep,
) -> NoteMetadataResponse:
    return service.get(note)


@router.get("", response_model=NoteMetadataResponse, include_in_schema=False)
def get_note_metadata_by_query(
    service: MetadataServiceDep,
    path: str = Query(...),
) -> NoteMetadataResponse:
    return service.get(path)


__all__ = ["router"]
