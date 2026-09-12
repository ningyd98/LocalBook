"""Stable RAG error types (M14 §十/§十七).

RAG failures are always *degradations of an optional derived layer*, never a
reason for the rest of LocalBook to stop working: the API maps
:class:`RagUnavailable` to HTTP 503 with a fixed code and a safe message (no
paths, no stack traces, no upstream bodies). Invalid user input keeps the
project-wide 400 ``invalid_request`` shape and is raised as
:class:`RagInvalidRequest`.
"""

from __future__ import annotations

from ..vault.errors import InvalidRequest


class RagError(Exception):
    """Base class for RAG domain errors."""

    code = "rag_error"
    status_code = 503

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code


class RagUnavailable(RagError):
    """The RAG stack/index cannot serve this request right now."""

    code = "rag_unavailable"

    def __init__(self, message: str = "RAG index is unavailable") -> None:
        super().__init__(message)


class RagDisabled(RagError):
    """RAG is switched off in settings."""

    code = "rag_disabled"


class RagEmbeddingUnavailable(RagError):
    """No embedding provider is configured/healthy (lexical path may still work)."""

    code = "embedding_unavailable"


class RagInvalidRequest(InvalidRequest):
    """Malformed RAG request (empty/oversized query, bad ranges).

    Reuses the project-wide ``invalid_request`` code so clients see one error
    vocabulary across every endpoint.
    """


__all__ = [
    "RagDisabled",
    "RagEmbeddingUnavailable",
    "RagError",
    "RagInvalidRequest",
    "RagUnavailable",
]
