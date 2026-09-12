"""Optional reranking (M14 §七).

Reranking is a *capability*, never a requirement: the hybrid retriever uses it
only when configured, and a reranker failure degrades the run instead of failing
it. M14 ships the protocol plus a deterministic local implementation so the
pipeline is testable offline; a cross-encoder or a remote service can implement
the same protocol later without changing the retrieval code.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(slots=True)
class RerankItem:
    """One reranked document: index into the input list plus its score."""

    index: int
    score: float


@runtime_checkable
class RerankProvider(Protocol):
    """Structural interface for a reranker."""

    @property
    def name(self) -> str: ...

    def rerank(
        self, query: str, documents: Sequence[str], *, top_n: int | None = None
    ) -> list[RerankItem]: ...


class LexicalOverlapReranker:
    """Deterministic local reranker based on term coverage and proximity.

    It is intentionally simple and honest: it is *not* a cross-encoder. It
    rewards chunks that contain more distinct query terms and penalises very
    long chunks (a weak precision signal), which measurably improves ordering
    when the fusion list mixes chunk sizes. The default configuration leaves
    reranking **disabled** because M14 must not require any extra service.
    """

    name = "lexical-overlap"

    def rerank(
        self, query: str, documents: Sequence[str], *, top_n: int | None = None
    ) -> list[RerankItem]:
        terms = [term for term in str(query).casefold().split() if term]
        scored: list[RerankItem] = []
        for index, document in enumerate(documents):
            text = str(document).casefold()
            if not terms:
                score = 0.0
            else:
                covered = sum(1 for term in terms if term in text)
                occurrences = sum(text.count(term) for term in terms)
                length_penalty = 1.0 / (1.0 + len(text) / 2000.0)
                score = (covered / len(terms)) * 2.0 + min(occurrences, 10) * 0.1
                score *= length_penalty
            scored.append(RerankItem(index=index, score=round(score, 6)))
        scored.sort(key=lambda item: (-item.score, item.index))
        if top_n is not None:
            return scored[: max(0, int(top_n))]
        return scored


__all__ = ["LexicalOverlapReranker", "RerankItem", "RerankProvider"]
