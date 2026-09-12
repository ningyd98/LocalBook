"""Vector (semantic) retrieval (M14 §六/§四).

The retriever owns only the *embedding of the query* and the call into the
vector store; the store owns the similarity scan. A missing embedding provider
is not an error: :class:`~server.rag.retrieval.base.RetrieverUnavailable` is
raised for the caller to record as a degraded path, because RAG must keep
working with lexical retrieval alone.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from ..embeddings.base import EmbeddingError, EmbeddingProvider
from ..embeddings.runner import EmbeddingRunner
from ..schemas import RetrievalResult
from ..vector.sqlite import RagStoreUnavailable, SqliteVectorStore
from .base import BaseRetriever, RetrieverUnavailable

logger = logging.getLogger("localnote.rag.retrieval.vector")

# Optional cosine floor for "there is no relevant passage".
#
# It defaults to 0.0 (disabled) on purpose: the useful threshold depends on the
# embedding model — the similarity distribution of a model that packs vectors
# into the positive orthant is nothing like one that does not — and a wrong
# floor silently discards real evidence. Operators who know their model can set
# ``rag.vector_min_score`` in settings (and tests pass an explicit floor).
DEFAULT_MIN_SCORE = 0.0


class VectorRetriever(BaseRetriever):
    """Embed the query, then scan the stored chunk vectors."""

    name = "vector"

    def __init__(
        self,
        store: SqliteVectorStore,
        *,
        provider: EmbeddingProvider | None = None,
        runner: EmbeddingRunner | None = None,
        min_score: float = DEFAULT_MIN_SCORE,
    ) -> None:
        self._store = store
        self._provider = provider
        self._runner = runner or (
            EmbeddingRunner(provider) if provider is not None else None
        )
        self.min_score = float(min_score)

    @property
    def available(self) -> bool:
        return self._runner is not None

    @property
    def model(self) -> str:
        return self._provider.model if self._provider is not None else ""

    def embed_query(self, query: str) -> list[float]:
        if self._runner is None:
            raise RetrieverUnavailable("No embedding provider is configured")
        try:
            vector = self._runner.embed_query(query)
        except EmbeddingError as exc:
            raise RetrieverUnavailable(f"Embedding query failed: {exc.code}") from exc
        if not vector:
            raise RetrieverUnavailable("Embedding provider returned an empty vector")
        return list(vector)

    def retrieve(
        self,
        query: str,
        *,
        top_k: int = 30,
        query_vector: Sequence[float] | None = None,
        allowed_paths: Sequence[str] | None = None,
    ) -> list[RetrievalResult]:
        if top_k <= 0:
            return []
        vector = list(query_vector) if query_vector is not None else self.embed_query(query)
        try:
            raw = self._store.search(
                vector, top_k=top_k, allowed_paths=allowed_paths
            )
        except RagStoreUnavailable as exc:
            raise RetrieverUnavailable("Vector store is unavailable") from exc
        # A cosine score below the floor means "nothing semantically close":
        # reporting it as evidence would let an unrelated note be cited, so the
        # vector path returns nothing and the answer stays honest.
        raw = [hit for hit in raw if float(hit.score) >= self.min_score]
        return [
            RetrievalResult(
                chunk_id=hit.chunk_id,
                path=hit.path,
                heading=hit.heading,
                heading_path=hit.section_path or hit.heading_path,
                content=hit.content,
                score=float(hit.score),
                vector_rank=hit.rank,
                start_line=hit.start_line,
                end_line=hit.end_line,
                tags=list(hit.tags),
                source="vector",
                content_hash=hit.content_hash,
            )
            for hit in raw
        ]

    def close(self) -> None:
        if self._runner is not None:
            self._runner.close()


__all__ = ["DEFAULT_MIN_SCORE", "VectorRetriever"]
