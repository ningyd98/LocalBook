"""RAG API/domain schemas (M14 §十).

These are the Pydantic models the HTTP layer exposes. They stay separate from
``server.rag.schemas`` (the internal dataclasses) so the wire contract can evolve
without changing retrieval internals, and separate from ``server.ai.schemas`` so
the M6 read-only AI contracts are untouched.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RagQueryRequest(_StrictModel):
    """``POST /api/v1/rag/query`` body."""

    query: str = Field(min_length=1, max_length=4000)
    top_k: int | None = Field(default=None, ge=1, le=50)
    rerank: bool | None = None
    debug: bool = False


class RagSearchRequest(_StrictModel):
    """``POST /api/v1/rag/search`` body (retrieval only, no chat model)."""

    query: str = Field(min_length=1, max_length=4000)
    top_k: int = Field(default=10, ge=1, le=50)
    # ``None`` follows the configured reranker; ``false`` forces the fused order.
    rerank: bool | None = None


class RagSource(_StrictModel):
    """One clickable citation of an answer (always produced by retrieval)."""

    id: str
    path: str
    heading: str | None = None
    heading_path: str | None = None
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    excerpt: str = ""
    score: float | None = None


class RagSearchHit(_StrictModel):
    # ``rank`` and ``source_id`` are server-derived display bindings. They let
    # graph/search UIs link a row to its evidence without parsing prose.
    rank: int = Field(ge=1)
    source_id: str
    chunk_id: str
    path: str
    heading: str | None = None
    heading_path: str | None = None
    excerpt: str
    score: float
    keyword_rank: int | None = None
    vector_rank: int | None = None
    rerank_score: float | None = None
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)


class RagRetrievalStats(_StrictModel):
    fts_candidates: int = 0
    vector_candidates: int = 0
    link_candidates: int = 0
    fused_candidates: int = 0
    reranked: bool = False
    context_chunks: int = 0
    context_tokens: int = 0
    retrieval_ms: float = 0.0
    embedding_ms: float = 0.0
    rerank_ms: float = 0.0
    generation_ms: float = 0.0
    degraded: list[str] = Field(default_factory=list)
    retrieval_debug: dict | None = None


class RagSearchResponse(_StrictModel):
    query: str
    results: list[RagSearchHit] = Field(default_factory=list)
    stats: RagRetrievalStats
    degraded: list[str] = Field(default_factory=list)
    generated_at: datetime


class RagQueryResponse(_StrictModel):
    query: str
    answer: str
    sources: list[RagSource] = Field(default_factory=list)
    # Compact, server-derived summary metadata for visual clients.
    evidence: dict[str, object] = Field(default_factory=dict)
    retrieval_stats: RagRetrievalStats
    model: str = ""
    prompt_version: str = ""
    degraded: list[str] = Field(default_factory=list)
    invalid_citations: list[str] = Field(default_factory=list)
    generated_at: datetime


class RagAnswerPayload(_StrictModel):
    """Structured model output (the body handed to the chat adapter)."""

    answer: str = Field(min_length=1, max_length=8000)
    used_sources: list[str] = Field(default_factory=list, max_length=20)


class RagIndexStatusResponse(_StrictModel):
    enabled: bool
    status: Literal["empty", "ready", "pending", "failed", "outdated"]
    embedding_provider: str = ""
    embedding_model: str = ""
    embedding_dimension: int = 0
    embedding_version: str = ""
    embedding_degraded: bool = False
    vector_store: str = ""
    vector_kernel: str = ""
    # Optional roadmap-③ link/graph path, relayed verbatim from the retriever:
    # "" when no link path is wired, "enabled" when it is wired and reachable,
    # otherwise the degraded reason (e.g. "link_unavailable"). The retriever owns
    # this answer — the service only reads it, and an empty value must render as
    # "nothing to report", never as a fabricated state.
    link_retrieval: str = ""
    indexed_notes: int = 0
    chunks: int = 0
    embedded_chunks: int = 0
    pending: int = 0
    failed: int = 0
    last_indexed: datetime | None = None
    chunk_target_tokens: int = 0
    chunk_max_tokens: int = 0
    message: str = ""


class RagIndexRebuildResponse(_StrictModel):
    indexed_documents: int = 0
    indexed_chunks: int = 0
    embedded_chunks: int = 0
    skipped_documents: int = 0
    failed_documents: int = 0
    duration_ms: float = 0.0
    ready: bool = False
    degraded: bool = False
    degraded_reason: str | None = None
    status: RagIndexStatusResponse


__all__ = [
    "RagAnswerPayload",
    "RagIndexRebuildResponse",
    "RagIndexStatusResponse",
    "RagQueryRequest",
    "RagQueryResponse",
    "RagRetrievalStats",
    "RagSearchHit",
    "RagSearchRequest",
    "RagSearchResponse",
    "RagSource",
]
