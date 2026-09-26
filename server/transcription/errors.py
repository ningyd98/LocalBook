"""Safe domain errors for local audio transcription."""

from __future__ import annotations

from enum import StrEnum


class TranscriptionErrorCode(StrEnum):
    DISABLED = "transcription_disabled"
    UNAVAILABLE = "transcription_unavailable"
    NOT_AUDIO = "not_audio"
    FAILED = "transcription_failed"
    TIMEOUT = "transcription_timeout"


class TranscriptionError(Exception):
    """Expected transcription failures with a stable API shape."""

    def __init__(
        self,
        code: TranscriptionErrorCode,
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


__all__ = ["TranscriptionError", "TranscriptionErrorCode"]
