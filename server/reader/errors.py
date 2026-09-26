"""Reader API domain errors."""

from __future__ import annotations

from enum import StrEnum


class ReaderErrorCode(StrEnum):
    """Reader-specific error codes."""

    # Device & Pairing
    DEVICE_NOT_FOUND = "device_not_found"
    INVALID_PAIRING_TOKEN = "invalid_pairing_token"
    PAIRING_TOKEN_EXPIRED = "pairing_token_expired"
    PAIRING_APPROVAL_REQUIRED = "pairing_approval_required"
    DEVICE_ALREADY_REGISTERED = "device_already_registered"
    INVALID_DEVICE_ID = "invalid_device_id"

    # Sync
    SYNC_CONFLICT = "sync_conflict"
    INVALID_CURSOR = "invalid_cursor"
    INVALID_OPERATION = "invalid_operation"
    BATCH_TOO_LARGE = "batch_too_large"

    # Resources
    SOURCE_NOT_FOUND = "source_not_found"
    SESSION_NOT_FOUND = "session_not_found"
    CONVERSATION_NOT_FOUND = "conversation_not_found"

    # General
    AI_UNAVAILABLE = "ai_unavailable"
    WEB_SEARCH_UNAVAILABLE = "web_search_unavailable"
    INVALID_REQUEST = "invalid_request"
    UNAUTHORIZED = "unauthorized"
    INTERNAL_ERROR = "internal_error"


class ReaderError(Exception):
    """Base exception for Reader API domain errors."""

    def __init__(
        self,
        code: ReaderErrorCode,
        message: str,
        status_code: int = 400,
        meta: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.meta = meta or {}
