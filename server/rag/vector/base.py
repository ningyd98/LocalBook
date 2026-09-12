"""Vector store abstraction (M14 §二).

The first implementation is SQLite-backed (``vector/sqlite.py``) because
LocalBook is local-first and must not depend on Qdrant/Elasticsearch/Milvus.
The interface is deliberately storage-agnostic so a future sqlite-vec or
embedded engine can be swapped in without touching retrieval, context building
or the API layer.
"""

from __future__ import annotations

import struct
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ..schemas import RAGChunk, RAGIndexState

VECTOR_FORMAT = "<{count}f"


def pack_vector(vector: Sequence[float]) -> bytes:
    """Pack one float32 vector (little-endian) for BLOB storage."""
    values = [float(value) for value in vector]
    return struct.pack(VECTOR_FORMAT.format(count=len(values)), *values)


def unpack_vector(blob: bytes, dimension: int) -> list[float]:
    """Unpack a BLOB written by :func:`pack_vector`."""
    if dimension <= 0:
        return []
    expected = 4 * dimension
    if len(blob) != expected:
        raise ValueError(
            f"vector blob length {len(blob)} does not match dimension {dimension}"
        )
    return list(struct.unpack(VECTOR_FORMAT.format(count=dimension), blob))


def pack_vectors(vectors: Sequence[Sequence[float]], dimension: int) -> bytes:
    """Pack a contiguous run of ``dimension``-sized vectors into one BLOB."""
    buffer = bytearray()
    for vector in vectors:
        if len(vector) != dimension:
            raise ValueError(
                f"vector length {len(vector)} does not match dimension {dimension}"
            )
        buffer.extend(pack_vector(vector))
    return bytes(buffer)


def iter_vectors(blob: bytes, dimension: int) -> Iterable[memoryview]:
    """Yield ``dimension``-sized slices of a packed vector run (zero copy)."""
    if dimension <= 0:
        return
    stride = 4 * dimension
    view = memoryview(blob)
    for start in range(0, len(view) - stride + 1, stride):
        yield view[start : start + stride]


@dataclass(slots=True)
class VectorHit:
    """One vector search result (``score`` is cosine similarity in [-1, 1])."""

    chunk_id: str
    path: str
    score: float
    rank: int = 0
    heading: str | None = None
    heading_path: str | None = None
    section_path: str | None = None
    start_line: int = 1
    end_line: int = 1
    content: str = ""
    content_hash: str = ""
    tags: list[str] = field(default_factory=list)


@runtime_checkable
class VectorStore(Protocol):
    """Storage contract used by the index service and the vector retriever."""

    def ensure_ready(self) -> None: ...

    def close(self) -> None: ...

    def delete_document(self, path: str) -> None: ...

    def upsert_chunks(self, chunks: Sequence[RAGChunk]) -> None: ...

    def upsert_embeddings(
        self,
        *,
        chunk_ids: Sequence[str],
        vectors: Sequence[Sequence[float]],
        model: str,
        embedding_version: str = "",
    ) -> None: ...

    def search(
        self,
        vector: Sequence[float],
        *,
        top_k: int = 30,
        allowed_paths: Sequence[str] | None = None,
    ) -> list[VectorHit]: ...

    def existing_embeddings(
        self,
        chunk_ids: Sequence[str],
        *,
        model: str,
        embedding_version: str = "",
    ) -> dict[str, str]: ...

    def chunks_without_embeddings(
        self, *, limit: int = 256, model: str, embedding_version: str = ""
    ) -> list[RAGChunk]: ...

    def chunk(self, chunk_id: str) -> RAGChunk | None: ...

    def document_hash(self, path: str) -> str | None: ...

    def document_content_hash(self, path: str) -> str | None: ...

    def document_paths(self) -> list[str]: ...

    def chunk_count(self) -> int: ...

    def embedded_count(self) -> int: ...

    def state(self) -> RAGIndexState: ...

    def save_state(self, state: RAGIndexState) -> None: ...

    def mark_documents_pending(self, paths: Sequence[str], reason: str) -> None: ...

    def reset(self) -> None: ...


__all__ = [
    "VectorHit",
    "VectorStore",
    "iter_vectors",
    "pack_vector",
    "pack_vectors",
    "unpack_vector",
]
