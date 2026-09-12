"""Embedding execution helper (M14 §四/§十三).

Embedding is async (HTTP) while the index service runs on watcher threads and
request threads. This module owns a single background event loop so every call
site can stay synchronous without creating a loop per call or fighting a caller's
running loop (``asyncio.run`` cannot be nested inside a running loop).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Sequence

from .base import EmbeddingError, EmbeddingProvider

logger = logging.getLogger("localnote.rag.embeddings")


class EmbeddingRunner:
    """Run ``EmbeddingProvider`` coroutines on a dedicated background loop."""

    def __init__(self, provider: EmbeddingProvider, *, timeout_seconds: float = 120.0):
        self._provider = provider
        self._timeout = float(timeout_seconds)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------

    @property
    def provider(self) -> EmbeddingProvider:
        return self._provider

    @property
    def model(self) -> str:
        return self._provider.model

    @property
    def dimension(self) -> int:
        return self._provider.dimension

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is not None and self._loop.is_running():
                return self._loop
            loop = asyncio.new_event_loop()
            thread = threading.Thread(
                target=self._run_loop, args=(loop,), name="rag-embedding", daemon=True
            )
            thread.start()
            self._loop = loop
            self._thread = thread
            return loop

    @staticmethod
    def _run_loop(loop: asyncio.AbstractEventLoop) -> None:
        asyncio.set_event_loop(loop)
        loop.run_forever()

    def _submit(self, coro):
        loop = self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        try:
            return future.result(timeout=self._timeout)
        except TimeoutError as exc:  # pragma: no cover - provider level timeout
            future.cancel()
            raise EmbeddingError("Embedding call timed out") from exc

    # ------------------------------------------------------------------

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._submit(self._provider.embed_documents(list(texts)))

    def embed_query(self, text: str) -> list[float]:
        return self._submit(self._provider.embed_query(text))

    def health_check(self) -> bool:
        try:
            return bool(self._submit(self._provider.health_check()))
        except Exception:  # pragma: no cover - provider errors are already typed
            return False

    def close(self) -> None:
        with self._lock:
            loop = self._loop
            self._loop = None
            self._thread = None
        if loop is not None:
            loop.call_soon_threadsafe(loop.stop)


__all__ = ["EmbeddingRunner"]
