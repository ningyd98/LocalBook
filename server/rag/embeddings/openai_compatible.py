"""OpenAI-compatible embedding provider (M14 §四).

Only this module performs HTTP for embeddings. It reuses the project's existing
``httpx`` dependency (no OpenAI SDK), enforces timeouts and a bounded response
size, and never logs request bodies, keys or full URLs with credentials.

Degradation contract: a missing/invalid endpoint raises
:class:`EmbeddingUnavailable`/:class:`EmbeddingRequestFailed`; callers treat that
as "RAG index pending/failed" and never let it break editing, FTS or startup.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Sequence
from typing import Any

import httpx

from .base import (
    EmbeddingDimensionMismatch,
    EmbeddingRequestFailed,
    EmbeddingUnavailable,
    normalize,
)

_DEFAULT_TIMEOUT = 30.0
_DEFAULT_MAX_RESPONSE_BYTES = 64 * 1024 * 1024


class OpenAICompatibleEmbeddingProvider:
    """``POST {base_url}/embeddings`` client for any OpenAI-compatible server.

    ``dimension`` is discovered from the first response and cached; callers may
    also pin it in configuration to detect a silent model change.
    """

    is_degraded = False

    def __init__(
        self,
        *,
        base_url: str | None,
        model: str,
        api_key: str | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT,
        batch_size: int = 32,
        dimension: int = 0,
        max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
        transport: httpx.AsyncBaseTransport | None = None,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
        extra_headers: dict[str, str] | None = None,
        trust_env: bool = False,
    ) -> None:
        self._base_url = (base_url or "").strip().rstrip("/") or None
        self._model = model.strip()
        self._api_key = (api_key or "").strip() or None
        self._timeout = float(timeout_seconds)
        self._batch_size = max(1, int(batch_size))
        self._dimension = int(dimension or 0)
        self._max_response_bytes = int(max_response_bytes)
        self._extra_headers = dict(extra_headers or {})
        self._transport = transport
        self._client_factory = client_factory
        # Shell proxy support is opt-in: see ``RagSettings.use_env_proxy``.
        self._trust_env = bool(trust_env)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def batch_size(self) -> int:
        return self._batch_size

    @property
    def configured(self) -> bool:
        return bool(self._base_url and self._model)

    # ------------------------------------------------------------------
    # HTTP plumbing
    # ------------------------------------------------------------------

    def _client(self) -> httpx.AsyncClient:
        if self._client_factory is not None:
            return self._client_factory()
        kwargs: dict[str, Any] = {
            "timeout": self._timeout,
            "trust_env": self._trust_env,
        }
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.AsyncClient(**kwargs)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", **self._extra_headers}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _endpoint(self) -> str:
        if not self.configured:
            raise EmbeddingUnavailable("Embedding endpoint is not configured")
        return f"{self._base_url}/embeddings"

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        endpoint = self._endpoint()
        try:
            async with self._client() as client:
                response = await client.post(
                    endpoint, json=payload, headers=self._headers()
                )
        except httpx.TimeoutException as exc:
            raise EmbeddingRequestFailed(
                "Embedding endpoint timed out", code="embedding_timeout"
            ) from exc
        except httpx.HTTPError as exc:
            raise EmbeddingRequestFailed(
                "Embedding endpoint is unreachable", code="embedding_unreachable"
            ) from exc

        if response.status_code in (401, 403):
            raise EmbeddingRequestFailed(
                "Embedding endpoint rejected the API key", code="embedding_auth_error"
            )
        if response.status_code >= 400:
            raise EmbeddingRequestFailed(
                f"Embedding endpoint returned HTTP {response.status_code}",
                code="embedding_http_error",
            )
        body = response.content
        if len(body) > self._max_response_bytes:
            raise EmbeddingRequestFailed(
                "Embedding response is too large", code="embedding_response_too_large"
            )
        try:
            parsed = json.loads(body)
        except (ValueError, UnicodeDecodeError) as exc:
            raise EmbeddingRequestFailed(
                "Embedding endpoint returned an invalid JSON body",
                code="embedding_invalid_response",
            ) from exc
        if not isinstance(parsed, dict):
            raise EmbeddingRequestFailed(
                "Embedding endpoint returned an unexpected body",
                code="embedding_invalid_response",
            )
        return parsed

    @staticmethod
    def _extract_vectors(payload: dict[str, Any], expected: int) -> list[list[float]]:
        data = payload.get("data")
        if not isinstance(data, list) or len(data) != expected:
            raise EmbeddingRequestFailed(
                "Embedding endpoint returned the wrong number of vectors",
                code="embedding_invalid_response",
            )
        vectors: list[list[float]] = []
        for item in data:
            vector = item.get("embedding") if isinstance(item, dict) else None
            if not isinstance(vector, list) or not vector:
                raise EmbeddingRequestFailed(
                    "Embedding endpoint returned a malformed vector",
                    code="embedding_invalid_response",
                )
            try:
                vectors.append([float(value) for value in vector])
            except (TypeError, ValueError) as exc:
                raise EmbeddingRequestFailed(
                    "Embedding endpoint returned a non-numeric vector",
                    code="embedding_invalid_response",
                ) from exc
        return vectors

    def _accept_vectors(self, vectors: list[list[float]]) -> list[list[float]]:
        if not vectors:
            return []
        found = len(vectors[0])
        if any(len(vector) != found for vector in vectors):
            raise EmbeddingRequestFailed(
                "Embedding endpoint returned inconsistent vector lengths",
                code="embedding_invalid_response",
            )
        if self._dimension and found != self._dimension:
            raise EmbeddingDimensionMismatch(
                f"expected {self._dimension}-dimensional embeddings, got {found}"
            )
        self._dimension = found
        return [normalize(_finite(vector, found)) for vector in vectors]

    # ------------------------------------------------------------------
    # Provider interface
    # ------------------------------------------------------------------

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        items = ["" if text is None else str(text) for text in texts]
        if not items:
            return []
        vectors: list[list[float]] = []
        for start in range(0, len(items), self._batch_size):
            window = items[start : start + self._batch_size]
            payload = await self._post({"model": self._model, "input": window})
            vectors.extend(self._accept_vectors(self._extract_vectors(payload, len(window))))
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        payload = await self._post({"model": self._model, "input": [str(text)]})
        vectors = self._accept_vectors(self._extract_vectors(payload, 1))
        return vectors[0] if vectors else []

    async def health_check(self) -> bool:
        try:
            await self.embed_query("health check")
        except Exception:
            return False
        return True


def _finite(vector: Sequence[float], expected: int) -> list[float]:
    values = list(vector)
    if len(values) != expected:
        raise EmbeddingDimensionMismatch(
            f"expected {expected}-dimensional embeddings, got {len(values)}"
        )
    for value in values:
        if math.isnan(value) or math.isinf(value):
            raise EmbeddingRequestFailed(
                "Embedding endpoint returned a non-finite value",
                code="embedding_invalid_response",
            )
    return values


__all__ = ["OpenAICompatibleEmbeddingProvider"]
