"""Hybrid retriever: keyword + vector (+ optional link/graph), fused with RRF.

The hybrid retriever is the only thing the RAG service talks to. It owns:

- candidate generation (``fts_top_k`` / ``vector_top_k`` / ``link_top_k``),
- rank fusion (RRF, never a raw score sum),
- the optional rerank stage,
- the final ``fusion_top_k`` cut.

Degradation is explicit and non-fatal: if embeddings are unavailable the fusion
simply runs with the keyword list (``degraded=["vector_unavailable"]``), which
is the difference between "RAG works with lexical evidence only" and "RAG is
broken". The link/graph path behaves the same way: ``link=None`` (the default,
and the pre-③ behaviour) does not touch the stats at all, while a configured link
retriever that cannot run appends its reason (``link_unavailable`` /
``link_failed``) and the fused ranking is built from the remaining lists.

The link list is the *only* one that is filtered before fusion, and only for the
default ``link_add_only=True``: graph neighbours that the direct paths already
returned are dropped, so the third path can add candidates but never re-rank
them (see :data:`DEFAULT_LINK_ADD_ONLY` for the measurement behind that default).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass

from ..rerank.base import RerankProvider
from ..schemas import RetrievalResult, RetrievalStats
from .base import BaseRetriever, RetrieverUnavailable
from .keyword import KeywordRetriever
from .rrf import DEFAULT_RRF_K, reciprocal_rank_fusion
from .vector import VectorRetriever

logger = logging.getLogger("localnote.rag.retrieval.hybrid")

# How much one link/graph rank is worth relative to a direct keyword/vector
# match. A graph neighbour was *not* matched by the query (it is connected to a
# note that was), so a second-hand vote must not be able to outvote a first-hand
# one. With the production knob values this is a strict inequality rather than a
# tuned coincidence:
#
#     0.5 / (60 + link_top_k=20) = 0.00625  <  1 / (60 + direct_top_k=30) = 0.0111
#
# i.e. even the best link-only candidate ranks below the *worst* direct one, so
# adding the link list cannot move a direct candidate down. The value is a
# constructor parameter, never a constant consulted by the code.
DEFAULT_LINK_RRF_WEIGHT = 0.5

# The link path is *additive* by default: a graph neighbour only joins the pool
# when neither the keyword nor the vector path returned that note.
#
# Measured reason (Golden Dataset, roadmap item ③): fusing the raw link list
# instead lifts notes the direct paths already ranked (a note at keyword rank 6
# + vector rank 7 gains a link vote and jumps to #1), which drove MRR from 0.917
# to 0.558 while recall stayed at 1.000. Expansion exists to *find missing*
# notes — a document both direct paths already returned needs no graph vote — so
# the default keeps the link list purely additive. ``link_add_only=False``
# restores the classic weighted fusion for whoever wants to measure it.
DEFAULT_LINK_ADD_ONLY = True


@dataclass(slots=True)
class HybridSearchOutcome:
    """Fused results plus the statistics of the run."""

    results: list[RetrievalResult]
    stats: RetrievalStats


class HybridRetriever(BaseRetriever):
    """Keyword + vector candidates fused by rank, then optionally reranked."""

    name = "hybrid"

    def __init__(
        self,
        *,
        keyword: KeywordRetriever,
        vector: VectorRetriever | None = None,
        link: BaseRetriever | None = None,
        reranker: RerankProvider | None = None,
        fts_top_k: int = 30,
        vector_top_k: int = 30,
        link_top_k: int = 20,
        link_rrf_weight: float = DEFAULT_LINK_RRF_WEIGHT,
        link_add_only: bool = DEFAULT_LINK_ADD_ONLY,
        fusion_top_k: int = 20,
        rerank_top_k: int = 10,
        rrf_k: int = DEFAULT_RRF_K,
    ) -> None:
        self._keyword = keyword
        self._vector = vector
        self._link = link
        self._reranker = reranker
        self.fts_top_k = max(1, int(fts_top_k))
        self.vector_top_k = max(1, int(vector_top_k))
        self.link_top_k = max(1, int(link_top_k))
        self.link_rrf_weight = max(0.0, float(link_rrf_weight))
        self.link_add_only = bool(link_add_only)
        self.fusion_top_k = max(1, int(fusion_top_k))
        self.rerank_top_k = max(1, int(rerank_top_k))
        self.rrf_k = max(1, int(rrf_k))

    # ------------------------------------------------------------------

    @property
    def reranker_enabled(self) -> bool:
        return self._reranker is not None

    @property
    def link_enabled(self) -> bool:
        """True when the optional link/graph path is wired into the fusion."""
        return self._link is not None

    @property
    def link_retrieval(self) -> str:
        """Status of the optional link/graph path: ``""`` | ``"enabled"`` | reason.

        - ``""`` — the path is not wired at all (the default, and today's
          behaviour);
        - ``"enabled"`` — wired and its graph source is reachable;
        - anything else — the degradation reason (e.g. ``"link_unavailable"``).

        Reachability is asked *directly* of the wired retriever
        (``LinkRetriever.link_retrieval``), never inferred from
        ``last_degraded``: that field is only written by a ``retrieve()`` call, so
        a fresh process that has not searched yet would report an unreachable
        graph as "enabled" — which is exactly the "pretend it is on" state this
        property exists to prevent. Per-query conditions (``link_no_seed``) belong
        to ``stats.degraded`` and are deliberately not part of this status.
        """
        if self._link is None:
            return ""
        probe = getattr(self._link, "link_retrieval", None)
        if isinstance(probe, str):
            return probe or "enabled"
        # A wired retriever that cannot describe its own reachability is reported
        # as wired: we never claim a degradation we cannot substantiate.
        return "enabled"

    def retrieve(
        self,
        query: str,
        *,
        top_k: int = 20,
        query_vector: Sequence[float] | None = None,
        allowed_paths: Sequence[str] | None = None,
    ) -> list[RetrievalResult]:
        return self.search(
            query,
            top_k=top_k,
            query_vector=query_vector,
            allowed_paths=allowed_paths,
        ).results

    def search(
        self,
        query: str,
        *,
        top_k: int | None = None,
        query_vector: Sequence[float] | None = None,
        allowed_paths: Sequence[str] | None = None,
        include_debug: bool = False,
        rerank: bool | None = None,
    ) -> HybridSearchOutcome:
        """Run keyword + vector retrieval, fuse, optionally rerank.

        ``rerank=None`` follows the configured reranker; ``rerank=False`` forces
        the fused ranking (a per-request override, never a global state change).
        """
        started = time.perf_counter()
        stats = RetrievalStats()
        resolved_top_k = int(top_k) if top_k is not None else self.fusion_top_k

        keyword_results: list[RetrievalResult] = []
        try:
            keyword_results = self._keyword.retrieve(
                query,
                top_k=self.fts_top_k,
                allowed_paths=allowed_paths,
            )
        except Exception:  # pragma: no cover - retriever is defensive already
            logger.exception("keyword retrieval failed")
            stats.degraded.append("keyword_failed")

        vector_results: list[RetrievalResult] = []
        if self._vector is not None:
            embedding_started = time.perf_counter()
            try:
                vector_results = self._vector.retrieve(
                    query,
                    top_k=self.vector_top_k,
                    query_vector=query_vector,
                    allowed_paths=allowed_paths,
                )
            except RetrieverUnavailable as exc:
                stats.degraded.append("vector_unavailable")
                logger.info("vector retrieval unavailable: %s", exc)
            except Exception:  # pragma: no cover - defensive
                logger.exception("vector retrieval failed")
                stats.degraded.append("vector_failed")
            finally:
                stats.embedding_ms = round(
                    (time.perf_counter() - embedding_started) * 1000.0, 3
                )
        else:
            stats.degraded.append("vector_disabled")

        link_results: list[RetrievalResult] = []
        raw_link_candidates = 0
        if self._link is not None:
            try:
                link_results = self._link.retrieve(
                    query,
                    top_k=self.link_top_k,
                    allowed_paths=allowed_paths,
                )
            except RetrieverUnavailable as exc:
                stats.degraded.append("link_unavailable")
                logger.info("link retrieval unavailable: %s", exc)
            except Exception:  # pragma: no cover - defensive
                logger.exception("link retrieval failed")
                stats.degraded.append("link_failed")
            else:
                # The link retriever reports its own (non-fatal) degradation,
                # e.g. "link_no_seed" when the query had no lexical anchor.
                marker = getattr(self._link, "last_degraded", None)
                if marker:
                    stats.degraded.append(str(marker))
                raw_link_candidates = len(link_results)
                if self.link_add_only:
                    link_results = self._new_link_candidates(
                        link_results, keyword_results, vector_results
                    )
                if self.link_add_only:
                    link_results = self._new_link_candidates(
                        link_results, keyword_results, vector_results
                    )

        stats.fts_candidates = len(keyword_results)
        stats.vector_candidates = len(vector_results)
        stats.link_candidates = len(link_results)
        want_rerank = (
            self._reranker is not None if rerank is None else bool(rerank)
        ) and self._reranker is not None
        ranked_lists: list[list[RetrievalResult]] = [keyword_results, vector_results]
        weights = [1.0, 1.0]
        if self._link is not None:
            ranked_lists.append(link_results)
            weights.append(self.link_rrf_weight)
        fused = reciprocal_rank_fusion(
            ranked_lists,
            k=self.rrf_k,
            top_k=(
                max(resolved_top_k, self.rerank_top_k)
                if want_rerank
                else resolved_top_k
            ),
            weights=weights,
        )
        stats.fused_candidates = len(fused)

        rerank_started = time.perf_counter()
        if want_rerank and fused:
            reranked = self._rerank(query, fused, stats)
            if reranked is not None:
                fused = reranked[:resolved_top_k]
                stats.reranked = True
        stats.rerank_ms = round((time.perf_counter() - rerank_started) * 1000.0, 3)
        stats.retrieval_ms = round((time.perf_counter() - started) * 1000.0, 3)
        if include_debug:
            stats.debug = {
                "keyword_path": getattr(self._keyword, "last_path", "none"),
                "fts_ranked": [
                    {"chunk_id": item.chunk_id, "rank": item.keyword_rank}
                    for item in keyword_results[:10]
                ],
                "vector_ranked": [
                    {"chunk_id": item.chunk_id, "rank": item.vector_rank}
                    for item in vector_results[:10]
                ],
            }
            if self._link is not None:
                # Only added when the path is configured, so the debug payload of
                # the pre-③ stack is unchanged.
                stats.debug["link_ranked"] = [
                    {"chunk_id": item.chunk_id, "rank": item.link_rank}
                    for item in link_results[:10]
                ]
                stats.debug["link_seeds"] = list(
                    getattr(self._link, "last_seed_paths", [])
                )
                stats.debug["link_candidates_raw"] = raw_link_candidates
        return HybridSearchOutcome(results=fused, stats=stats)

    # ------------------------------------------------------------------

    @staticmethod
    def _new_link_candidates(
        link_results: list[RetrievalResult],
        keyword_results: list[RetrievalResult],
        vector_results: list[RetrievalResult],
    ) -> list[RetrievalResult]:
        """Keep only link candidates whose *document* the direct paths missed.

        The link path is a recall expansion: a note the lexical or semantic path
        already returned is already ranked there by that (direct) evidence, so it
        must not also collect a graph vote. That is not a stylistic choice — it is
        the measurement behind :data:`DEFAULT_LINK_ADD_ONLY`, and the experiment
        is reproducible through the harness diagnostics
        (``python -m tests.rag.eval.run --link-diagnostics``, rows labelled
        ``DIAGNOSTIC link_add_only=False …``):

        - configuration: ``HybridRetriever(link=…, link_add_only=False)`` fused
          with every raw link hit (i.e. the non-additive variant, *not* the
          shipped default and not reachable from settings);
        - result on the Golden Dataset: an incidental graph edge lifted notes the
          direct paths had already ranked (a note at keyword rank 6 + vector rank
          7 became #1) and MRR fell from 0.917 to 0.558, while recall stayed
          1.000. The additive default keeps that configuration out of the product
          path, which is why it is neutral (0.917) rather than harmful.

        Filtering by ``path`` (not by chunk id) also drops a second chunk of an
        already-retrieved note, so the fused ranking cannot contain the same
        document twice. Ranks are renumbered over the surviving list so RRF sees a
        contiguous ranking.
        """
        covered = {item.path for item in keyword_results}
        covered.update(item.path for item in vector_results)
        kept = [item for item in link_results if item.path not in covered]
        for rank, item in enumerate(kept, start=1):
            item.link_rank = rank
        return kept

    def _rerank(
        self, query: str, candidates: list[RetrievalResult], stats: RetrievalStats
    ) -> list[RetrievalResult] | None:
        """Rerank the fused list; a failing reranker never breaks retrieval."""
        assert self._reranker is not None
        subset = candidates[: max(self.rerank_top_k * 2, self.rerank_top_k)]
        try:
            ordered = self._reranker.rerank(
                query,
                [item.content for item in subset],
                top_n=min(self.rerank_top_k, len(subset)),
            )
        except Exception as exc:  # pragma: no cover - provider specific
            logger.warning("rerank failed: %s", type(exc).__name__)
            stats.degraded.append("rerank_failed")
            return None
        by_index = list(subset)
        reranked: list[RetrievalResult] = []
        for item in ordered:
            index = getattr(item, "index", None)
            score = getattr(item, "score", None)
            if index is None or not (0 <= int(index) < len(by_index)):
                continue
            chosen = by_index[int(index)]
            chosen.rerank_score = float(score) if score is not None else None
            reranked.append(chosen)
        if not reranked:
            stats.degraded.append("rerank_empty")
            return None
        # Keep the RRF tail that the reranker did not see, so a shortlist never
        # silently shrinks below fusion_top_k.
        seen = {item.chunk_id for item in reranked}
        for item in candidates:
            if item.chunk_id not in seen:
                reranked.append(item)
        return reranked


__all__ = ["HybridRetriever", "HybridSearchOutcome"]
