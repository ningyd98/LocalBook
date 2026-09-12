"""Reciprocal Rank Fusion (M14 §六).

BM25 scores and cosine similarities live on incomparable scales, so M14 never
adds them linearly. RRF combines *ranks* instead::

    score(chunk) = Σ_sources  1 / (k + rank_source(chunk))

with ``k`` defaulting to 60 (the value from the original RRF paper, kept as a
configurable knob). A chunk found by both retrievers therefore outranks a chunk
found by only one, which is exactly the "hybrid beats either path" behaviour the
acceptance criteria require. The same formula accepts the optional link/graph
list (roadmap item ③) without any special case: the list is just one more ranked
source, and its own internal weights decide that list's order.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from ..schemas import RetrievalResult

DEFAULT_RRF_K = 60

# The per-retriever rank attribute consulted (in this order) before falling back
# to the position inside its own list. ``link_rank`` was added by the link/graph
# path; lists that never set it behave exactly as before.
RANK_ATTRIBUTES = ("keyword_rank", "vector_rank", "link_rank")


def effective_rank(item, position: int) -> int:
    """Rank a ranked list contributed for ``item`` (1-based, position fallback)."""
    for attribute in RANK_ATTRIBUTES:
        rank = getattr(item, attribute, None)
        if rank:
            return int(rank)
    return int(position)


def reciprocal_rank_fusion(
    ranked_lists: Sequence[Sequence[RetrievalResult]],
    *,
    k: int = DEFAULT_RRF_K,
    top_k: int | None = None,
    weights: Sequence[float] | None = None,
) -> list[RetrievalResult]:
    """Fuse ranked result lists into one ranking (best first).

    ``weights`` optionally scales each list's contribution
    (``score = Σ_list w_list / (k + rank)``); ``None`` means every list weighs
    1.0, which is the plain RRF used before the link path existed. A list of
    indirect evidence (graph neighbours) can therefore stay additive without
    being able to outvote a direct lexical/semantic match.

    Ties break deterministically by ``chunk_id`` so repeated runs over an
    unchanged index return an identical order.
    """
    if k <= 0:
        raise ValueError("RRF k must be positive")
    lists = list(ranked_lists)
    resolved_weights = (
        [1.0 for _ in lists] if weights is None else [float(item) for item in weights]
    )
    if len(resolved_weights) != len(lists):
        raise ValueError("weights must match the number of ranked lists")
    merged: dict[str, RetrievalResult] = {}
    scores: dict[str, float] = {}
    for results, weight in zip(lists, resolved_weights, strict=True):
        if weight <= 0.0:
            continue
        for position, item in enumerate(results, start=1):
            effective = effective_rank(item, position)
            scores[item.chunk_id] = scores.get(item.chunk_id, 0.0) + weight * (
                1.0 / (k + effective)
            )
            existing = merged.get(item.chunk_id)
            if existing is None:
                merged[item.chunk_id] = item
                continue
            # Keep the first occurrence's content (sources agree on it) but
            # carry every rank/provider that found the chunk.
            if existing.keyword_rank is None and item.keyword_rank is not None:
                existing.keyword_rank = item.keyword_rank
            if existing.vector_rank is None and item.vector_rank is not None:
                existing.vector_rank = item.vector_rank
            if existing.link_rank is None and getattr(item, "link_rank", None) is not None:
                existing.link_rank = getattr(item, "link_rank", None)
            if not existing.content and item.content:
                existing.content = item.content
                existing.start_line = item.start_line
                existing.end_line = item.end_line
                existing.heading = item.heading
                existing.heading_path = item.heading_path
                existing.content_hash = item.content_hash
                existing.document_level = getattr(item, "document_level", False)
    ordered = sorted(
        merged.values(), key=lambda item: (-scores[item.chunk_id], item.chunk_id)
    )
    for item in ordered:
        item.score = round(scores[item.chunk_id], 8)
    if top_k is not None:
        return ordered[: max(0, int(top_k))]
    return ordered


def fuse_scores(scores: Iterable[tuple[str, int]], *, k: int = DEFAULT_RRF_K) -> dict[str, float]:
    """Low-level helper: fuse ``(chunk_id, rank)`` pairs into RRF scores."""
    totals: dict[str, float] = {}
    for chunk_id, rank in scores:
        totals[chunk_id] = totals.get(chunk_id, 0.0) + 1.0 / (k + max(1, int(rank)))
    return totals


__all__ = [
    "DEFAULT_RRF_K",
    "RANK_ATTRIBUTES",
    "effective_rank",
    "fuse_scores",
    "reciprocal_rank_fusion",
]
