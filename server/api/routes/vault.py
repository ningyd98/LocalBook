"""REST endpoints for the M1 Vault core.

Handlers in this module are intentionally thin: they validate request DTOs,
call the dependency-injected ``VaultService``, and serialize safe metadata.
They never open files, traverse directories, or manipulate ``Path`` objects.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from ...vault.schemas import (
    FileCreateRequest,
    FileDeleteRequest,
    FileMoveRequest,
    FileMutationResponse,
    FileReadResponse,
    FileWriteRequest,
    VaultFileTreeResponse,
)
from ...vault.service import VaultService
from ..dependencies import get_vault_service

router = APIRouter(prefix="/api/v1/vault", tags=["vault"])
VaultServiceDep = Annotated[VaultService, Depends(get_vault_service)]


@router.get("/files", response_model=VaultFileTreeResponse)
def list_vault_files(
    service: VaultServiceDep,
    path: str | None = Query(default=None),
    recursive: bool = Query(default=True),
    include_hidden: bool = Query(default=False),
) -> VaultFileTreeResponse:
    entries = service.list_tree(
        path,
        recursive=recursive,
        include_hidden=include_hidden,
    )
    return VaultFileTreeResponse(
        root=".",
        entries=entries,
        generated_at=datetime.now(UTC),
    )


@router.get("/file", response_model=FileReadResponse)
def read_vault_file(
    service: VaultServiceDep,
    path: str = Query(...),
) -> FileReadResponse:
    result = service.read_file(path)
    return FileReadResponse(
        path=result["path"],
        content_base64=result["content_base64"],
        byte_length=result["byte_length"],
        sha256=result["sha256"],
        content_type=result["content_type"],
    )


@router.post(
    "/file",
    response_model=FileMutationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_vault_file(
    request: FileCreateRequest,
    service: VaultServiceDep,
) -> FileMutationResponse:
    result = service.create_bytes(request.path, request.content_base64)
    return FileMutationResponse(**result)


@router.patch("/file", response_model=FileMutationResponse)
def update_vault_file(
    request: FileWriteRequest,
    service: VaultServiceDep,
) -> FileMutationResponse:
    result = service.write_bytes(
        request.path,
        request.content_base64,
        request.expected_sha256,
    )
    return FileMutationResponse(**result)


@router.delete("/file", response_model=FileMutationResponse)
def delete_vault_file(
    request: FileDeleteRequest,
    service: VaultServiceDep,
) -> FileMutationResponse:
    result = service.delete_file(request.path, request.expected_sha256)
    return FileMutationResponse(**result)


@router.post("/file/move", response_model=FileMutationResponse)
def move_vault_file(
    request: FileMoveRequest,
    service: VaultServiceDep,
) -> FileMutationResponse:
    result = service.move_file(
        request.source_path,
        request.destination_path,
        request.expected_sha256,
    )
    return FileMutationResponse(**result)


__all__ = ["router"]
