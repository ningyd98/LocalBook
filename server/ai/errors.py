"""Safe domain errors for read-only AI workflows."""

from __future__ import annotations

from enum import StrEnum


class AIErrorCode(StrEnum):
    DISABLED = "ai_disabled"
    NOT_CONFIGURED = "ai_not_configured"
    UNAVAILABLE = "ai_unavailable"
    TIMEOUT = "ai_timeout"
    MODEL_NOT_FOUND = "ai_model_not_found"
    CAPABILITY_UNAVAILABLE = "ai_capability_unavailable"
    INVALID_OUTPUT = "ai_invalid_output"
    INVALID_REQUEST = "ai_invalid_request"
    CONTEXT_TOO_LARGE = "ai_context_too_large"
    NOT_FOUND = "not_found"
    INDEX_UNAVAILABLE = "index_unavailable"
    INTERNAL_ERROR = "internal_error"


class AIError(Exception):
    def __init__(
        self,
        code: AIErrorCode,
        message: str,
        *,
        status_code: int | None = None,
        meta: dict[str, str] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.meta: dict[str, str] = meta or {}
        self.status_code = status_code or (
            {
                AIErrorCode.INVALID_OUTPUT: 502,
                AIErrorCode.INVALID_REQUEST: 400,
                AIErrorCode.NOT_FOUND: 404,
            }.get(code, 503)
        )


class AIAdapterError(AIError):
    def __init__(
        self,
        kind: str,
        message: str = "AI provider unavailable",
        *,
        http_status: int | None = None,
    ):
        self.kind = kind
        self.http_status = http_status
        code = {
            "timeout": AIErrorCode.TIMEOUT,
            "capability_unavailable": AIErrorCode.CAPABILITY_UNAVAILABLE,
            "model_not_found": AIErrorCode.MODEL_NOT_FOUND,
            "invalid_response": AIErrorCode.INVALID_OUTPUT,
            "http_error": AIErrorCode.UNAVAILABLE,
            "offline": AIErrorCode.UNAVAILABLE,
        }.get(kind, AIErrorCode.UNAVAILABLE)
        status = 502 if kind == "invalid_response" else 400 if kind == "model_not_found" else None
        super().__init__(code, message, status_code=status)


class CapabilityUnavailable(AIAdapterError):
    def __init__(self):
        super().__init__("capability_unavailable", "AI capability is unavailable")
