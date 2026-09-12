"""OpenAI-compatible / Cohere-style reranker adapter (M14 §七).

Reranking stays optional and additive: this adapter is only constructed when the
operator enables it *and* configures a model, it is the sole HTTP boundary for
reranking, and every failure is reported as a degradation instead of an error —
retrieval must keep working when the reranker is down.

Two response shapes are accepted because the ecosystem has not settled on one:

- ``{"results": [{"index": 0, "relevance_score": 0.9}, ...]}`` (Jina/Cohere/vLLM);
- ``{"data": [{"index": 0, "score": 0.9}, ...]}`` (some local servers).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Sequence
from typing import Any

import httpx

from .base import RerankItem, RerankProvider

logger = logging.getLogger("localnote.rag.rerank.openai")

_DEFAULT_TIMEOUT = 30.0
_DEFAULT_MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class RerankUnavailable(RuntimeError):
    """The reranker cannot answer (missing config or unreachable endpoint)."""


class OpenAICompatibleReranker(RerankProvider):
    """``POST {base_url}/rerank`` client for any OpenAI/Cohere-compatible server."""

    name = "openai_compatible"

    def __init__(
        self,
        *,
        base_url: str | None,
        model: str,
        api_key: str | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT,
        max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
        transport: httpx.AsyncBaseTransport | None = None,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
        max_documents: int = 64,
        trust_env: bool = False,
    ) -> None:
        self._base_url = (base_url or "").strip().rstrip("/") or None
        self._model = model.strip()
        self._api_key = (api_key or "").strip() or None
        self._timeout = float(timeout_seconds)
        self._max_bytes = int(max_response_bytes)
        self._transport = transport
        self._client_factory = client_factory
        self._max_documents = max(1, int(max_documents))
        # Shell proxy support is opt-in: see ``RagSettings.use_env_proxy``.
        self._trust_env = bool(trust_env)

    @property
    def configured(self) -> bool:
        return bool(self._base_url and self._model)

    # ------------------------------------------------------------------

    def rerank(
        self, query: str, documents: Sequence[str], *, top_n: int | None = None
    ) -> list[RerankItem]:
        """Blocking API used by the synchronous retriever (bounded by timeout).

        The HTTP call is async internally (``httpx.AsyncClient``), so an event
        loop is created for the duration of the call when none is running; the
        retriever and the index service are synchronous by design (LocalBook is
        a single-process local server), and this keeps one adapter for both.
        """
        if not self.configured:
            raise RerankUnavailable("Reranker endpoint is not configured")
        items = list(documents)[: self._max_documents]
        if not items:
            return []
        payload = {"model": self._model, "query": str(query), "documents": items}
        if top_n is not None:
            payload["top_n"] = max(1, int(top_n))
        body = self._run(self._post(payload))
        ranked = parse_rerank_response(body, len(items))
        if top_n is not None:
            return ranked[: max(1, int(top_n))]
        return ranked

    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

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

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        endpoint = f"{self._base_url}/rerank"
        try:
            async with self._client() as client:
                response = await client.post(
                    endpoint, json=payload, headers=self._headers()
                )
        except httpx.TimeoutException as exc:
            raise RerankUnavailable("Reranker timed out") from exc
        except httpx.HTTPError as exc:
            raise RerankUnavailable("Reranker is unreachable") from exc
        if response.status_code in (401, 403):
            raise RerankUnavailable("Reranker rejected the API key")
        if response.status_code >= 400:
            raise RerankUnavailable(f"Reranker returned HTTP {response.status_code}")
        raw = response.content
        if len(raw) > self._max_bytes:
            raise RerankUnavailable("Reranker response is too large")
        try:
            parsed = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise RerankUnavailable("Reranker returned an invalid JSON body") from exc
        if not isinstance(parsed, dict):
            raise RerankUnavailable("Reranker returned an unexpected body")
        return parsed

    @staticmethod
    def _run(coro):
        """Run one coroutine from synchronous code (no running loop assumed)."""
        import asyncio

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        # Inside a loop (e.g. an async test): run it on a private loop in a
        # worker thread so the caller's loop is never nested into.
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()

    async def health_check(self) -> bool:
        try:
            await self._post({"model": self._model, "query": "ping", "documents": ["ping"]})
        except RerankUnavailable:
            return False
        return True


def parse_rerank_response(payload: dict[str, Any], document_count: int) -> list[RerankItem]:
    """Normalise a rerank response into ranked :class:`RerankItem` rows.

    Unknown shapes raise :class:`RerankUnavailable` so the caller degrades rather
    than silently trusting an arbitrary order.
    """
    rows = payload.get("results")
    if not isinstance(rows, list):
        rows = payload.get("data")
    if not isinstance(rows, list) or not rows:
        raise RerankUnavailable("Reranker returned no results")
    items: list[RerankItem] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        index = row.get("index")
        score = row.get("relevance_score", row.get("score"))
        if index is None or score is None:
            continue
        try:
            resolved_index = int(index)
            resolved_score = float(score)
        except (TypeError, ValueError):
            continue
        if 0 <= resolved_index < document_count:
            items.append(RerankItem(index=resolved_index, score=resolved_score))
    if not items:
        raise RerankUnavailable("Reranker returned no usable rows")
    items.sort(key=lambda item: (-item.score, item.index))
    return items


__all__ = ["OpenAICompatibleReranker", "RerankUnavailable", "parse_rerank_response"]
