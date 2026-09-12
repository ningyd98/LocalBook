"""M14 RAG core schemas (derived data only).

Every structure here describes **derived** retrieval data: nothing in
``server.rag`` may write to the Vault, and every row/artifact can be rebuilt
from the Markdown files alone (``.localnote/`` is disposable).

Design invariants (M14 §B):
- ``RAGChunk`` carries the exact ``start_line``/``end_line`` of the source
  text so a citation never points outside the file it names;
- ``content_hash`` is the chunk text hash and is used for incremental
  embedding (unchanged chunk ⇒ no re-embedding);
- ``EvidencePack`` is the single grounding boundary shared by the RAG answer,
  future Agents and Related Notes.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, runtime_checkable

RetrieverSource = Literal["fts", "vector", "link", "graph"]


def content_hash(content: str) -> str:
    """SHA-256 of chunk/document text (deterministic, never salted)."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class RAGChunk:
    """One embedding/citation unit produced by the Markdown-aware chunker."""

    chunk_id: str
    document_id: str  # == vault-relative path; no synthetic ids
    path: str
    content: str
    content_hash: str
    heading: str | None = None
    heading_path: str | None = None
    # Deepest section the chunk's text ends in; ``heading_path`` names the
    # section that *owns* the chunk (the shallowest heading inside it).
    section_path: str | None = None
    start_offset: int = 0
    end_offset: int = 0
    start_line: int = 1
    end_line: int = 1
    tags: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    modified_at: datetime | None = None
    chunk_index: int = 0
    token_estimate: int = 0

    @property
    def embedding_text(self) -> str:
        """Text handed to the embedding model (never stored as ``content``).

        The heading breadcrumb is part of the embedding input so a chunk under
        ``云边协同 > 重规划机制`` also retrieves for the section title, while
        ``content`` keeps the byte-exact source lines for citations.
        """
        parts: list[str] = [f"文件：{self.path}"]
        section = self.section_path or self.heading_path
        if section:
            parts.append(f"章节：{section}")
        if self.tags:
            parts.append("标签：" + " ".join(self.tags))
        parts.append("正文：")
        parts.append(self.content)
        return "\n".join(parts)


@dataclass(slots=True)
class ChunkMetadata:
    """Per-chunk embedding bookkeeping (kept out of the FTS/index tables)."""

    embedding_version: str = ""
    dimension: int = 0
    semantic_score: float | None = None
    rerank_score: float | None = None


@dataclass(slots=True)
class RetrievalResult:
    """One retriever hit before ContextBuilder packing.

    ``keyword_rank``/``vector_rank``/``link_rank`` are 1-based ranks inside the
    producing retriever (``None`` when that retriever did not return the chunk);
    they are what RRF consumes and what ``/rag/search`` exposes for debugging.

    ``document_level`` marks a hit that names a *document* instead of an indexed
    chunk (the link retriever's fallback when the RAG index has no chunk for that
    path). Such a hit carries the ``doc::<path>`` pseudo id and no
    line-accurate range, so it must never be rendered as a line citation.
    """

    chunk_id: str
    path: str
    heading: str | None
    heading_path: str | None
    content: str
    score: float = 0.0
    keyword_rank: int | None = None
    vector_rank: int | None = None
    link_rank: int | None = None
    rerank_score: float | None = None
    start_line: int = 1
    end_line: int = 1
    tags: list[str] = field(default_factory=list)
    source: RetrieverSource = "fts"
    content_hash: str = ""
    document_level: bool = False


@dataclass(slots=True)
class SourceEvidence:
    """One citation entry of an :class:`EvidencePack` (``S1``, ``S2`` …)."""

    source_id: str
    path: str
    heading: str | None = None
    heading_path: str | None = None
    start_line: int = 1
    end_line: int = 1
    content: str = ""
    content_hash: str = ""
    chunk_id: str = ""

    @property
    def excerpt(self) -> str:
        return self.content


@dataclass(slots=True)
class EvidencePack:
    """The grounding boundary: exactly what the LLM is allowed to use."""

    query: str
    sources: list[SourceEvidence] = field(default_factory=list)
    context_tokens: int = 0
    candidate_count: int = 0
    truncated: bool = False

    @property
    def source_ids(self) -> list[str]:
        return [source.source_id for source in self.sources]

    @property
    def paths(self) -> list[str]:
        seen: list[str] = []
        for source in self.sources:
            if source.path not in seen:
                seen.append(source.path)
        return seen

    def source(self, source_id: str) -> SourceEvidence | None:
        for item in self.sources:
            if item.source_id == source_id:
                return item
        return None


@dataclass(slots=True)
class CitationReport:
    """Outcome of validating generated ``[S1]`` markers against the pack."""

    valid_ids: list[str] = field(default_factory=list)
    invalid_ids: list[str] = field(default_factory=list)
    resolved_ids: list[str] = field(default_factory=list)

    @property
    def has_invalid(self) -> bool:
        return bool(self.invalid_ids)


@dataclass(slots=True)
class RAGIndexState:
    """Persistent, rebuildable state of the vector index (``rag_index_state``)."""

    embedding_provider: str = ""
    embedding_model: str = ""
    embedding_dimension: int = 0
    embedding_version: str = ""
    indexed_documents: int = 0
    chunk_count: int = 0
    last_indexed_at: datetime | None = None
    status: Literal["empty", "ready", "pending", "failed", "outdated"] = "empty"
    pending: int = 0
    failed: int = 0

    def is_compatible_with(
        self,
        *,
        provider: str,
        model: str,
        dimension: int,
        version: str = "",
    ) -> bool:
        """True when the stored index was built by the same embedding config.

        A different provider/model/dimension/version means every stored vector
        is meaningless, so the caller must mark the index ``outdated`` and
        re-embed instead of querying stale vectors (M14 §四).
        """
        if self.status == "outdated":
            return False
        if self.status == "empty" or not self.chunk_count:
            return True  # nothing stored yet: any config can build it
        return (
            self.embedding_provider == provider
            and self.embedding_model == model
            and int(self.embedding_dimension) == int(dimension)
            and self.embedding_version == version
        )


@dataclass(slots=True)
class RetrievalStats:
    """Observability payload for one retrieval run (M14 §十八)."""

    fts_candidates: int = 0
    vector_candidates: int = 0
    # Link/graph candidates that actually entered the fusion (after the additive
    # filter, when the hybrid retriever runs in its default ``link_add_only``
    # mode). Always 0 when the link path is not configured.
    link_candidates: int = 0
    fused_candidates: int = 0
    reranked: bool = False
    context_chunks: int = 0
    context_tokens: int = 0
    retrieval_ms: float = 0.0
    embedding_ms: float = 0.0
    rerank_ms: float = 0.0
    generation_ms: float = 0.0
    degraded: list[str] = field(default_factory=list)
    debug: dict[str, Any] | None = None


@runtime_checkable
class Chunker(Protocol):
    """Structural interface for Markdown-aware chunkers."""

    def chunk_document(
        self,
        path: str,
        text: str,
        *,
        modified_at: datetime | None = None,
    ) -> list[RAGChunk]: ...


__all__ = [
    "ChunkMetadata",
    "Chunker",
    "CitationReport",
    "EvidencePack",
    "RAGChunk",
    "RAGIndexState",
    "RetrievalResult",
    "RetrievalStats",
    "RetrieverSource",
    "SourceEvidence",
    "content_hash",
    "utcnow",
]
