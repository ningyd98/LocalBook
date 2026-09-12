"""Embedding providers for RAG (M14 §四).

The embedding model is explicitly **decoupled** from the chat model: LocalBook
never assumes that the configured chat model can produce embeddings, and the
two may point at completely different endpoints/models.

Contract (structural ``EmbeddingProvider``):

- ``embed_documents(texts)`` — batch embed, returns one vector per input;
- ``embed_query(text)`` — single embed for a search query;
- ``health_check()`` — cheap availability probe;
- ``dimension`` — discovered on first successful call, never hardcoded.

All HTTP lives in ``openai_compatible.py``; this module only defines the
interface, its error types and an in-memory provider used by tests and by
installations that have not configured an embedding endpoint yet.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
from collections.abc import Sequence
from typing import Protocol, runtime_checkable


class EmbeddingError(RuntimeError):
    """Base class for embedding failures (never carries secrets)."""

    code = "embedding_error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code:
            self.code = code


class EmbeddingUnavailable(EmbeddingError):
    """No embedding provider is configured (RAG degrades, never crashes)."""

    code = "embedding_unavailable"


class EmbeddingRequestFailed(EmbeddingError):
    """The provider answered with an error, a timeout or an invalid body."""

    code = "embedding_request_failed"


class EmbeddingDimensionMismatch(EmbeddingError):
    """The provider returned vectors of an unexpected dimension."""

    code = "embedding_dimension_mismatch"


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Structural interface implemented by every embedding backend."""

    @property
    def model(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...

    async def health_check(self) -> bool: ...


def normalize(vector: Sequence[float]) -> list[float]:
    """L2-normalise a vector so cosine similarity == dot product.

    Stored vectors are normalised at write time, which keeps the SQLite scan
    cheap (a plain dot product) and makes scores comparable across providers.
    """
    norm = math.sqrt(sum(float(value) * float(value) for value in vector))
    if norm == 0.0:
        return [0.0 for _ in vector]
    return [float(value) / norm for value in vector]


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise EmbeddingDimensionMismatch(
            f"vector length mismatch: {len(left)} != {len(right)}"
        )
    dot = sum(float(a) * float(b) for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(float(a) * float(a) for a in left))
    right_norm = math.sqrt(sum(float(b) * float(b) for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


class HashEmbeddingProvider:
    """Deterministic, offline embedding provider.

    Used as the default when no embedding endpoint is configured and by the
    whole test suite (no network, no model download). It is a *character
    n-gram hashing* embedding: semantically weak but deterministic and
    dimension-configurable, so it proves the pipeline end to end while being
    documented as ``degraded`` in ``/rag/index/status``.

    It never pretends to be a real model: ``is_degraded`` is True and the
    status endpoint surfaces that fact.
    """

    is_degraded = True

    def __init__(self, *, dimension: int = 256, model: str = "local-hash") -> None:
        self._dimension = max(16, int(dimension))
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimension(self) -> int:
        return self._dimension

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self._dimension
        folded = text.casefold()
        for size in (1, 2, 3):
            for index in range(0, max(0, len(folded) - size + 1)):
                gram = folded[index : index + size]
                digest = hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest()
                slot = int.from_bytes(digest[:4], "big") % self._dimension
                sign = 1.0 if digest[4] % 2 == 0 else -1.0
                vector[slot] += sign
        return normalize(vector)

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(str(text)) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._vector(str(text))

    async def health_check(self) -> bool:
        return True


class MockEmbeddingProvider:
    """Recording stub: deterministic vectors + call bookkeeping for tests."""

    is_degraded = False

    def __init__(
        self,
        *,
        dimension: int = 8,
        model: str = "mock-embed",
        fail_on_call: int | None = None,
    ) -> None:
        self._dimension = int(dimension)
        self._model = model
        self.document_calls: list[list[str]] = []
        self.query_calls: list[str] = []
        self.health_calls = 0
        self._fail_on_call = fail_on_call
        self._call_counter = 0

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimension(self) -> int:
        return self._dimension

    def _vector(self, text: str) -> list[float]:
        """Distinct vector per text, with near-duplicates scoring higher.

        ``text`` is hashed both whole and per character n-gram; mixing the two
        digests with a position-dependent factor gives lexically similar inputs
        a measurably higher cosine similarity while keeping the provider fully
        deterministic and offline (it is a *test* double, not a semantic model).
        """
        raw: list[float] = []
        whole = hashlib.sha256(text.encode("utf-8")).digest()
        folded = text.casefold()
        for index in range(self._dimension):
            mix = 0
            for size in (1, 2):
                gram = folded[index % max(1, len(folded)) :][:size] or folded[:size] or " "
                digest = hashlib.blake2b(
                    gram.encode("utf-8"), digest_size=4
                ).digest()
                mix = (mix * 31 + int.from_bytes(digest, "big")) % (2**31)
            raw.append(float((whole[index % len(whole)] + mix) % 251))
        return normalize(raw)

    def _maybe_fail(self) -> None:
        self._call_counter += 1
        if self._fail_on_call is not None and self._call_counter == self._fail_on_call:
            raise EmbeddingRequestFailed("mock embedding failure", code="mock_failure")

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self._maybe_fail()
        batch = [str(text) for text in texts]
        self.document_calls.append(batch)
        return [self._vector(text) for text in batch]

    async def embed_query(self, text: str) -> list[float]:
        self._maybe_fail()
        self.query_calls.append(text)
        return self._vector(text)

    async def health_check(self) -> bool:
        self.health_calls += 1
        return True


class FailingEmbeddingProvider:
    """Always-failing provider: models "embedding endpoint is down"."""

    is_degraded = True

    def __init__(self, *, dimension: int = 8, model: str = "failing-embed") -> None:
        self._dimension = int(dimension)
        self._model = model
        self.calls = 0

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimension(self) -> int:
        return self._dimension

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        raise EmbeddingRequestFailed("embedding endpoint is down", code="embedding_unreachable")

    async def embed_query(self, text: str) -> list[float]:
        self.calls += 1
        raise EmbeddingRequestFailed("embedding endpoint is down", code="embedding_unreachable")

    async def health_check(self) -> bool:
        return False


class BatchingEmbeddingProvider:
    """Wrap a provider so large indexing runs are split into bounded batches.

    Batching is what keeps a local embedding server responsive and makes
    progress reportable/interruptible during a full-vault rebuild.
    """

    def __init__(self, provider: EmbeddingProvider, *, batch_size: int = 32) -> None:
        self._provider = provider
        self._batch_size = max(1, int(batch_size))

    @property
    def inner(self) -> EmbeddingProvider:
        return self._provider

    @property
    def model(self) -> str:
        return self._provider.model

    @property
    def dimension(self) -> int:
        return self._provider.dimension

    @property
    def batch_size(self) -> int:
        return self._batch_size

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        items = [str(text) for text in texts]
        if not items:
            return []
        vectors: list[list[float]] = []
        for start in range(0, len(items), self._batch_size):
            window = items[start : start + self._batch_size]
            vectors.extend(await self._provider.embed_documents(window))
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        return await self._provider.embed_query(text)

    async def health_check(self) -> bool:
        return await self._provider.health_check()


async def gather_embeddings(
    provider: EmbeddingProvider,
    texts: Sequence[str],
    *,
    concurrency: int = 1,
) -> list[list[float]]:
    """Embed ``texts`` preserving order, optionally with bounded concurrency."""
    if concurrency <= 1 or len(texts) <= 1:
        return await provider.embed_documents(texts)
    chunks = [
        list(texts[index : index + concurrency])
        for index in range(0, len(texts), concurrency)
    ]
    results: list[list[float]] = []
    for chunk in chunks:
        results.extend(await provider.embed_documents(chunk))
    return results


def run_sync(coro):
    """Run one coroutine from sync code (index service / watcher threads)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise EmbeddingError("embedding cannot be awaited inside a running loop")


__all__ = [
    "BatchingEmbeddingProvider",
    "EmbeddingDimensionMismatch",
    "EmbeddingError",
    "EmbeddingProvider",
    "EmbeddingRequestFailed",
    "EmbeddingUnavailable",
    "FailingEmbeddingProvider",
    "HashEmbeddingProvider",
    "MockEmbeddingProvider",
    "cosine_similarity",
    "gather_embeddings",
    "normalize",
    "run_sync",
]
