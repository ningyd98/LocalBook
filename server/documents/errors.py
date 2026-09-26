"""Safe domain errors for local document previews."""

from __future__ import annotations

from enum import StrEnum


class DocumentPreviewErrorCode(StrEnum):
    DISABLED = "document_preview_disabled"
    UNAVAILABLE = "document_preview_unavailable"
    NOT_PREVIEWABLE = "not_previewable"
    FAILED = "document_preview_failed"
    TIMEOUT = "document_preview_timeout"
    TOO_LARGE = "document_preview_too_large"


class DocumentPreviewError(Exception):
    """Expected document-preview failures with a stable API shape."""

    def __init__(
        self,
        code: DocumentPreviewErrorCode,
        message: str,
        *,
        status_code: int = 503,
        path: str | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        self.path = path
        super().__init__(message)


__all__ = ["DocumentPreviewError", "DocumentPreviewErrorCode"]
