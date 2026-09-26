"""Note export endpoints.

``GET /api/v1/export/note`` returns the manifest the web app renders before
printing a PDF; ``GET /api/v1/export/markdown`` returns the self-contained
``.md`` file itself. Both are read-only: the Vault is opened through
``VaultService`` and never written.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from ...export.schemas import ExportAttachment, ExportNoteResponse, ExportWarning
from ...export.service import EXPORT_SERVICE_VERSION, ExportService
from ..dependencies import get_export_service

router = APIRouter(prefix="/api/v1/export", tags=["export"])
ExportServiceDep = Annotated[ExportService, Depends(get_export_service)]


def _disposition(filename: str) -> str:
    """``attachment`` disposition that survives non-ASCII note names.

    Header values are latin-1 on the wire, so the UTF-8 name only ever travels
    through the RFC 5987 ``filename*`` parameter; the bare ``filename`` stays a
    sanitised ASCII fallback.
    """
    ascii_name = "".join(
        char if char.isascii() and char not in '"\\' and 31 < ord(char) < 127 else "_"
        for char in filename
    )
    if not ascii_name or set(ascii_name) == {"_"}:
        ascii_name = "note.md"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename, safe='')}"


def _manifest(service: ExportService, path: str) -> ExportNoteResponse:
    result = service.prepare(path)
    return ExportNoteResponse(
        path=result.path,
        title=result.title,
        download_name=result.download_name,
        markdown=result.markdown,
        attachments=[
            ExportAttachment(
                ref=item.ref,
                url=item.url,
                path=item.path,
                mime=item.mime,
                size=item.size,
                data_uri=item.data_uri,
                inlined=item.inlined,
                reason=item.reason,
            )
            for item in result.attachments
        ],
        warnings=[ExportWarning(**warning) for warning in result.warnings],
        inlined_bytes=result.inlined_bytes,
        truncated=result.truncated,
        service_version=EXPORT_SERVICE_VERSION,
        generated_at=result.generated_at,
    )


@router.get("/note", response_model=ExportNoteResponse)
def export_note(
    service: ExportServiceDep,
    path: Annotated[str, Query(min_length=1, max_length=4096)],
) -> ExportNoteResponse:
    """Manifest for one note: Vault-relative references plus attachment payloads."""
    return _manifest(service, path)


@router.get("/markdown")
def export_markdown(
    service: ExportServiceDep,
    path: Annotated[str, Query(min_length=1, max_length=4096)],
) -> Response:
    """The note as a single ``.md`` file with attachments inlined as data URIs."""
    result = service.prepare(path)
    body = result.inlined_markdown.encode("utf-8")
    return Response(
        content=body,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": _disposition(result.download_name),
            "Content-Length": str(len(body)),
            # Lets the UI report what was embedded without re-parsing the file.
            "X-Export-Attachments": str(sum(1 for item in result.attachments if item.inlined)),
            "X-Export-Skipped": str(sum(1 for item in result.attachments if not item.inlined)),
            "X-Export-Truncated": "1" if result.truncated else "0",
            "X-Export-Generated-At": datetime.now(UTC).isoformat(),
        },
    )


__all__ = ["router"]
