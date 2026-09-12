"""REST endpoints for the Vault trash (soft delete + restore + retention).

``DELETE /vault/file`` stays the byte-level primitive; these endpoints are the
user-facing recycle bin the UI uses instead, so a mis-click can be undone for the
configured retention window (default 30 days).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends

from ...vault.schemas import FileMutationResponse
from ...vault.trash import TrashService, validate_trash_id
from ...vault.trash_schemas import (
    TrashEntryResponse,
    TrashListResponse,
    TrashRequest,
    TrashRestoreRequest,
)
from ..dependencies import get_trash_service

router = APIRouter(prefix="/api/v1/trash", tags=["trash"])
TrashServiceDep = Annotated[TrashService, Depends(get_trash_service)]


def _entry_response(service: TrashService, item) -> TrashEntryResponse:
    view = service.view(item)
    return TrashEntryResponse(
        id=view.item.id,
        original_path=view.item.original_path,
        name=view.item.name,
        kind=view.item.kind,  # type: ignore[arg-type]
        byte_length=view.item.byte_length,
        file_count=view.item.file_count,
        deleted_at=view.item.deleted_at,
        expires_at=view.expires_at,
        days_remaining=view.days_remaining,
    )


@router.get("", response_model=TrashListResponse)
def list_trash(service: TrashServiceDep) -> TrashListResponse:
    views = service.list_entries()
    entries = [_entry_response(service, view.item) for view in views]
    return TrashListResponse(
        entries=entries,
        count=len(entries),
        total_bytes=sum(entry.byte_length for entry in entries),
        retention_days=service.retention_days,
        generated_at=datetime.now(UTC),
    )


@router.post("", response_model=TrashEntryResponse, status_code=201)
def move_to_trash(request: TrashRequest, service: TrashServiceDep) -> TrashEntryResponse:
    """Move one file or folder into the trash (a folder is moved as a whole)."""
    item = service.trash(request.path, request.expected_sha256)
    return _entry_response(service, item)


@router.post("/{entry_id}/restore", response_model=FileMutationResponse)
def restore_trash_entry(
    entry_id: str,
    request: TrashRestoreRequest,
    service: TrashServiceDep,
) -> FileMutationResponse:
    restored, _renamed = service.restore(validate_trash_id(entry_id), rename_if_occupied=request.rename_if_occupied)
    # ``operation`` stays inside the M1 enum; a renamed restore is reported
    # through the returned path, which the client opens as-is.
    return FileMutationResponse(path=restored, sha256=None, byte_length=None, operation="moved")


@router.delete("/{entry_id}", response_model=FileMutationResponse)
def delete_trash_entry(entry_id: str, service: TrashServiceDep) -> FileMutationResponse:
    """Remove one entry permanently."""
    original = service.delete(validate_trash_id(entry_id))
    return FileMutationResponse(path=original, sha256=None, byte_length=None, operation="deleted")


@router.delete("", response_model=TrashListResponse)
def empty_trash(service: TrashServiceDep) -> TrashListResponse:
    """Remove every entry permanently; the emptied bin is returned."""
    service.empty()
    return list_trash(service)


__all__ = ["router"]
