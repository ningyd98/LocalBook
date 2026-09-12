"""Retriever abstraction (M14 §六/§十五).

Every candidate source implements the same thin contract so the hybrid
retriever, the context builder and the API never depend on a particular search
engine. ``KeywordRetriever``, ``VectorRetriever``, ``HybridRetriever`` and
``LinkRetriever`` (wikilink / backlink / tag / graph-neighbour boosts) all
implement this protocol and plug into the RRF fusion without any of them having
to know about the others.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from ..embeddings.base import EmbeddingError
from ..schemas import RetrievalResult


class RetrieverUnavailable(RuntimeError):
    """The retriever cannot run right now (index missing, provider down)."""


@runtime_checkable
class Retriever(Protocol):
    """Structural interface for one candidate source."""

    @property
    def name(self) -> str: ...

    def retrieve(
        self,
        query: str,
        *,
        top_k: int = 30,
        query_vector: Sequence[float] | None = None,
        allowed_paths: Sequence[str] | None = None,
    ) -> list[RetrievalResult]: ...


class BaseRetriever:
    """Shared helpers for concrete retrievers."""

    name = "base"

    def retrieve(
        self,
        query: str,
        *,
        top_k: int = 30,
        query_vector: Sequence[float] | None = None,
        allowed_paths: Sequence[str] | None = None,
    ) -> list[RetrievalResult]:
        raise NotImplementedError


__all__ = ["BaseRetriever", "EmbeddingError", "Retriever", "RetrieverUnavailable"]
