"""REST endpoints for the M1 Vault core.

Handlers in this module are intentionally thin: they validate request DTOs,
call the dependency-injected ``VaultService``, and serialize safe metadata.
They never open files, traverse directories, or manipulate ``Path`` objects.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse

from ...config import ATTACHMENT_JSON_MAX_BYTES
from ...vault.errors import FileTooLarge, InvalidRequest
from ...vault.schemas import (
    AttachmentUploadRequest,
    AttachmentUploadResponse,
    DirectoryCreateRequest,
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

_RESOURCE_CHUNK_SIZE = 1024 * 1024


def _parse_range_header(value: str | None, total: int) -> tuple[int, int] | None:
    """Parse one RFC 7233 byte range and map invalid ranges to HTTP 416."""
    if value is None:
        return None
    invalid = HTTPException(
        status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
        detail="Requested byte range is not satisfiable",
        headers={"Content-Range": f"bytes */{total}", "Accept-Ranges": "bytes"},
    )
    if not value.startswith("bytes=") or "," in value:
        raise invalid
    spec = value[6:].strip()
    if "-" not in spec:
        raise invalid
    start_text, end_text = (part.strip() for part in spec.split("-", 1))
    if not start_text and not end_text:
        raise invalid
    try:
        if not start_text:
            suffix_length = int(end_text)
            if suffix_length <= 0:
                raise invalid
            start = max(total - suffix_length, 0)
            end = total - 1
        else:
            start = int(start_text)
            end = min(int(end_text), total - 1) if end_text else total - 1
    except (TypeError, ValueError):
        raise invalid from None
    if total <= 0 or start < 0 or start >= total or end < start:
        raise invalid
    return start, end


def _seek_resource(resource: dict[str, Any], start: int) -> None:
    if start <= 0:
        return
    try:
        resource["reader"].seek(start)
    except (AttributeError, OSError) as exc:
        owner = resource.get("owner")
        if owner is not None:
            owner.close_resource(resource)
        raise HTTPException(
            status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
            detail="Requested byte range is not satisfiable",
            headers={
                "Content-Range": f"bytes */{resource['byte_length']}",
                "Accept-Ranges": "bytes",
            },
        ) from exc


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


@router.post(
    "/directory",
    response_model=FileMutationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_vault_directory(
    request: DirectoryCreateRequest,
    service: VaultServiceDep,
) -> FileMutationResponse:
    result = service.create_directory(request.path)
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


# ---------------------------------------------------------------------------
# Attachment uploads (user direct upload only; never routed through Policy)
# ---------------------------------------------------------------------------


@router.post(
    "/attachments",
    response_model=AttachmentUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
def upload_vault_attachment(
    request: AttachmentUploadRequest,
    service: VaultServiceDep,
) -> AttachmentUploadResponse:
    """JSON/base64 channel for small attachments (≤ ATTACHMENT_JSON_MAX_BYTES)."""
    data = request.content_base64
    if len(data) > ATTACHMENT_JSON_MAX_BYTES:
        raise FileTooLarge()
    result = service.upload_attachment_bytes(
        request.original_name,
        request.target_directory,
        data,
    )
    return AttachmentUploadResponse(**result)


@router.post(
    "/attachments/multipart",
    response_model=AttachmentUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
def upload_vault_attachment_multipart(
    service: VaultServiceDep,
    file: Annotated[UploadFile, File()],
    # ``target_directory`` is required by contract, but an empty string means
    # the Vault root and a browser multipart encoder omits empty fields, so a
    # missing field is treated as the root rather than rejected.
    target_directory: Annotated[str, Form()] = "",
    original_name: Annotated[str | None, Form()] = None,
) -> AttachmentUploadResponse:
    """Streaming channel for large attachments; the payload is never buffered.

    The route only hands a bounded reader to the service — it never opens,
    resolves or writes a filesystem path itself.
    """
    name = (original_name or file.filename or "").strip()
    if not name:
        raise InvalidRequest("original_name must be a non-empty string")
    result = service.upload_attachment_stream(
        name,
        target_directory,
        _upload_reader(file),
    )
    return AttachmentUploadResponse(**result)


class _UploadReader:
    """Bounded blocking reader over ``UploadFile.file``.

    ``upload_attachment_stream`` only ever calls ``read(chunk_size)``; the
    underlying SpooledTemporaryFile serves small payloads from memory and large
    ones from disk, so the service never aggregates the whole upload.
    """

    def __init__(self, upload: UploadFile) -> None:
        self._upload = upload

    def read(self, size: int = _RESOURCE_CHUNK_SIZE) -> bytes:
        data = self._upload.file.read(size)
        return data or b""


def _upload_reader(upload: UploadFile) -> _UploadReader:
    return _UploadReader(upload)


def _stream_resource(resource: dict[str, Any], *, length: int | None = None) -> Iterator[bytes]:
    reader = resource["reader"]
    remaining = int(resource["byte_length"] if length is None else length)
    limit = int(resource["max_bytes"])
    total = 0
    try:
        while remaining > 0:
            chunk = reader.read(min(_RESOURCE_CHUNK_SIZE, remaining))
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                # The file grew between the size check and the read; stop
                # rather than streaming an unbounded body.
                raise FileTooLarge()
            remaining -= len(chunk)
            yield chunk
    finally:
        service = resource.get("owner")
        if service is not None:
            service.close_resource(resource)


@router.get("/resource")
def read_vault_resource(
    request: Request,
    service: VaultServiceDep,
    path: str = Query(...),
) -> Response:
    """Read-only raw bytes for preview/download with single-range support."""
    resource = service.open_resource(path)
    try:
        byte_range = _parse_range_header(
            request.headers.get("range"), int(resource["byte_length"])
        )
    except Exception:
        service.close_resource(resource)
        raise
    start = 0
    length: int | None = None
    status_code = status.HTTP_200_OK
    headers = {
        "Content-Disposition": resource["content_disposition"],
        "Accept-Ranges": "bytes",
        # Short caching only: an external change to the file must become
        # visible, and the bytes are not content-addressed/immutable.
        "Cache-Control": "no-cache",
        "X-Content-Type-Options": "nosniff",
    }
    if byte_range is not None:
        start, end = byte_range
        length = end - start + 1
        _seek_resource(resource, start)
        status_code = status.HTTP_206_PARTIAL_CONTENT
        headers["Content-Range"] = f"bytes {start}-{end}/{resource['byte_length']}"
    headers["Content-Length"] = str(resource["byte_length"] if length is None else length)
    return StreamingResponse(
        _stream_resource(resource, length=length),
        status_code=status_code,
        media_type=resource["content_type"],
        headers=headers,
    )


@router.head("/resource", include_in_schema=False)
def head_vault_resource(
    service: VaultServiceDep,
    path: str = Query(...),
) -> Response:
    """Metadata-only HEAD; reuses the same service validation and size check."""
    resource = service.open_resource(path)
    try:
        return Response(
            status_code=status.HTTP_200_OK,
            media_type=resource["content_type"],
            headers={
                "Content-Length": str(resource["byte_length"]),
                "Content-Disposition": resource["content_disposition"],
                "Accept-Ranges": "bytes",
                "Cache-Control": "no-cache",
                "X-Content-Type-Options": "nosniff",
            },
        )
    finally:
        service.close_resource(resource)


__all__ = ["router"]
