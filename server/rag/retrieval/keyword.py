"""Keyword (lexical) retrieval (M14 §六).

LocalBook already owns a SQLite FTS5 index. M14 adds a **chunk-level** FTS table
(``rag_chunks_fts``) so a hit can name the exact section it came from, and falls
back to the existing document-level FTS (``notes_fts`` via
``DerivedIndexService``) and finally to a casefolded substring scan when FTS5 is
unavailable or the query cannot be tokenised (Chinese short queries).

The fallback order is fixed and documented, because "FTS found nothing" and
"FTS unavailable" must both stay usable: lexical retrieval is never the single
point of failure of the RAG path.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Sequence

from ...index.service import DerivedIndexService
from ..chunking.markdown import estimate_tokens
from ..schemas import RetrievalResult
from ..vector.sqlite import RagStoreUnavailable, SqliteVectorStore
from .base import BaseRetriever

# Character-bigram coverage a query term must reach to count as a lexical match
# (0.5 = at least half of the term's adjacent character pairs must appear). A
# lower value trades precision for recall: the Golden Dataset evaluation measures
# both, and this constant is pinned by its tests.
CJK_MIN_COVERAGE = 0.5

_MAX_QUERY_LENGTH = 256
_MAX_TERM_LENGTH = 64
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# A term may ride the FTS path only when every character is ASCII tokenizable
# (unicode61 output); CJK/emoji/accented terms go to the substring path, the
# same rule the M4 SearchService documents.
_FTS_SAFE_TERM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


# Typographic punctuation a user types but a note rarely contains verbatim
# (Chinese full-width marks included). Terms are stripped of it so `云边协同？`
# still matches a sentence that ends in `云边协同。`.
_TERM_TRIM = (
    " \t\r\n"
    ".,;:!?，。、；：！？…·"
    "\"'“”‘’"
    "()（）[]【】{}<>《》"
    "—–-*#`|/\\"
)


def tokenize(query: str) -> list[str]:
    """Split a query into casefolded terms (order preserved, de-duplicated).

    Leading/trailing punctuation is trimmed: without it a Chinese question such
    as 哪篇笔记讨论了快慢双系统？ would become one unmatchable term and the
    lexical path would return nothing at all.
    """
    terms: list[str] = []
    for raw in str(query).split():
        term = raw.strip(_TERM_TRIM).casefold()
        if term and term not in terms:
            terms.append(term)
    return terms


def validate_query(query: str) -> None:
    """Reject empty/oversized/control-character queries (HTTP 400 upstream)."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("Query must not be empty")
    if len(query) > _MAX_QUERY_LENGTH:
        raise ValueError("Query is too long")
    if _CONTROL_RE.search(query):
        raise ValueError("Query contains control characters")
    for term in query.split():
        if len(term) > _MAX_TERM_LENGTH:
            raise ValueError("Query term is too long")


def _has_cjk(text: str) -> bool:
    return any("㐀" <= char <= "鿿" for char in text)


def _gram_variants(term: str, size: int) -> list[str]:
    """Overlapping ``size``-character windows of one term (de-duplicated)."""
    variants: list[str] = []
    seen: set[str] = set()
    for start in range(0, max(0, len(term) - size + 1)):
        candidate = term[start : start + size]
        if candidate not in seen:
            seen.add(candidate)
            variants.append(candidate)
    return variants


def cjk_ngram_groups(
    terms: Sequence[str], *, core_gram: int = 3
) -> list[list[str]]:
    """One OR-group of 3-character windows per long CJK term (groups are ANDed).

    Selectivity matters: a query like 量子计算与鸟类迁徙的关系 must not match a
    note about 番茄种植 just because it shares one common character. Requiring a
    3-character window per query term is selective for Chinese while still
    tolerating a different word order than the note happens to use.
    """
    groups: list[list[str]] = []
    for term in terms:
        if _has_cjk(term) and len(term) >= core_gram:
            groups.append(_gram_variants(term, core_gram))
    return groups


def expand_cjk_terms(
    terms: Sequence[str], *, max_terms: int = 24, max_gram: int = 3
) -> list[str]:
    """Split long CJK runs into n-grams the substring path can match.

    Chinese is written without spaces, so ``云边协同机械臂`` is one whitespace
    token that only matches a byte-identical span. Overlapping n-grams
    (longest first) let the lexical path find the *relevant passage* instead of
    requiring the whole phrase verbatim; ASCII terms are passed through
    unchanged.
    """
    expanded: list[str] = []
    seen: set[str] = set()
    for gram_size in range(max_gram, 0, -1):
        for term in terms:
            if not _has_cjk(term) or len(term) <= gram_size:
                continue
            for start in range(0, len(term) - gram_size + 1):
                candidate = term[start : start + gram_size]
                if candidate not in seen:
                    seen.add(candidate)
                    expanded.append(candidate)
    for term in terms:
        if not _has_cjk(term) and term not in seen:
            seen.add(term)
            expanded.append(term)
    return expanded[:max_terms]


def fts_match_expression(terms: Sequence[str]) -> str | None:
    """Quoted-phrase FTS5 expression (implicit AND), or None when unusable."""
    cleaned = [str(term) for term in terms if str(term).strip()]
    if not cleaned or not all(_FTS_SAFE_TERM_RE.match(term) for term in cleaned):
        return None
    return " ".join(f'"{term}"' for term in cleaned)


class KeywordRetriever(BaseRetriever):
    """Chunk-level FTS5 → document-level FTS5 → substring fallback."""

    name = "keyword"

    def __init__(
        self,
        store: SqliteVectorStore,
        *,
        index: DerivedIndexService | None = None,
    ) -> None:
        self._store = store
        self._index = index
        self.last_path = "none"

    # ------------------------------------------------------------------

    def retrieve(
        self,
        query: str,
        *,
        top_k: int = 30,
        query_vector: Sequence[float] | None = None,
        allowed_paths: Sequence[str] | None = None,
    ) -> list[RetrievalResult]:
        terms = tokenize(query)
        if not terms or top_k <= 0:
            return []
        allowed = set(allowed_paths) if allowed_paths is not None else None

        hits = self._chunk_fts(terms, top_k=top_k)
        if hits:
            self.last_path = "chunk_fts"
            return self._filter(hits, allowed)
        hits = self._document_fts(terms, top_k=top_k)
        if hits:
            self.last_path = "document_fts"
            return self._filter(hits, allowed)
        hits = self._substring(terms, top_k=top_k)
        self.last_path = "substring" if hits else "none"
        return self._filter(hits, allowed)

    # ------------------------------------------------------------------

    @staticmethod
    def _filter(
        hits: list[RetrievalResult], allowed: set[str] | None
    ) -> list[RetrievalResult]:
        if allowed is None:
            return hits
        return [hit for hit in hits if hit.path in allowed]

    def _chunk_fts(self, terms: Sequence[str], *, top_k: int) -> list[RetrievalResult]:
        expression = fts_match_expression(terms)
        if expression is None:
            return []
        try:
            raw = self._store.chunk_fts_search(expression, limit=top_k)
        except RagStoreUnavailable:
            return []
        return [
            RetrievalResult(
                chunk_id=hit.chunk_id,
                path=hit.path,
                heading=hit.heading,
                heading_path=hit.section_path or hit.heading_path,
                content=hit.content,
                score=hit.score,
                keyword_rank=hit.rank,
                start_line=hit.start_line,
                end_line=hit.end_line,
                tags=list(hit.tags),
                source="fts",
                content_hash=hit.content_hash,
            )
            for hit in raw
        ]

    def _document_fts(
        self, terms: Sequence[str], *, top_k: int
    ) -> list[RetrievalResult]:
        """Document-level FTS from M4, with the chunk containing the hit.

        Used when the RAG chunk index has no matching row (for example before
        the first RAG index build). The result still names the *chunk* that
        contains the term so downstream citations stay line-accurate.
        """
        if self._index is None:
            return []
        expression = fts_match_expression(terms)
        if expression is None:
            return []
        candidates = self._index.fts_search(expression)[:top_k]
        results: list[RetrievalResult] = []
        for candidate in candidates:
            chunk = self._best_chunk(candidate.path, terms)
            if chunk is None:
                results.append(
                    RetrievalResult(
                        chunk_id=f"doc::{candidate.path}",
                        path=candidate.path,
                        heading=candidate.title,
                        heading_path=candidate.title,
                        content=candidate.text[:2000],
                        score=float(candidate.score or 0.0),
                        keyword_rank=len(results) + 1,
                        source="fts",
                    )
                )
                continue
            results.append(chunk)
        return results

    def _best_chunk(self, path: str, terms: Sequence[str]) -> RetrievalResult | None:
        try:
            chunks = self._store.chunks_for_document(path)
        except RagStoreUnavailable:
            return None
        if not chunks:
            return None
        folded = [term for term in terms]
        best = None
        best_score = -1
        for chunk in chunks:
            haystack = chunk.content.casefold()
            score = sum(1 for term in folded if term in haystack)
            score += sum(1 for term in folded if term in (chunk.heading or "").casefold())
            if score > best_score:
                best, best_score = chunk, score
        if best is None:
            return None
        return RetrievalResult(
            chunk_id=best.chunk_id,
            path=best.path,
            heading=best.heading,
            heading_path=best.section_path or best.heading_path,
            content=best.content,
            score=float(best_score),
            keyword_rank=1,
            start_line=best.start_line,
            end_line=best.end_line,
            tags=list(best.tags),
            source="fts",
            content_hash=best.content_hash,
        )

    def _substring(
        self, terms: Sequence[str], *, top_k: int
    ) -> list[RetrievalResult]:
        """Lexical fallback: verbatim match, then CJK bigram coverage.

        Two tiers, because a natural-language question carries words the note
        legitimately does not contain:

        1. every term verbatim — a whole-run query such as 番茄种植需要多少光照
           matches a note titled 番茄种植笔记 through this;
        2. CJK terms by bigram coverage — 哪篇笔记记录了异网异构下的调度问题
           finds 异网异构 even though the question never appears verbatim.

        Terms are dropped one at a time, longest-first, and a term is only ever
        dropped while at least one distinctive term remains: relaxing must not
        reduce a question to a single generic word.
        """
        cleaned = [term for term in terms if term]
        if not cleaned:
            return []
        for dropped in range(0, len(cleaned)):
            subset = [term for term in cleaned if term not in cleaned[:dropped]]
            if not subset:
                break
            try:
                raw = self._store.substring_chunk_search(
                    subset, limit=top_k, min_coverage=0.0
                )
                if not raw:
                    raw = self._store.substring_chunk_search(
                        subset, limit=top_k, min_coverage=CJK_MIN_COVERAGE
                    )
            except (RagStoreUnavailable, sqlite3.Error):
                return []
            if raw:
                return self._to_results(raw)
        return []

    @staticmethod
    def _to_results(raw) -> list[RetrievalResult]:
        return [
            RetrievalResult(
                chunk_id=hit.chunk_id,
                path=hit.path,
                heading=hit.heading,
                heading_path=hit.section_path or hit.heading_path,
                content=hit.content,
                score=float(hit.score),
                keyword_rank=rank,
                start_line=hit.start_line,
                end_line=hit.end_line,
                tags=list(hit.tags),
                source="fts",
                content_hash=hit.content_hash,
            )
            for rank, hit in enumerate(raw, start=1)
        ]


def rerank_by_frequency(
    results: list[RetrievalResult], terms: Sequence[str]
) -> list[RetrievalResult]:
    """Order substring hits by raw occurrence count (stable tiebreak by path)."""
    scored = sorted(
        results,
        key=lambda item: (
            -sum(item.content.casefold().count(term) for term in terms),
            item.path.casefold(),
        ),
    )
    for rank, item in enumerate(scored, start=1):
        item.keyword_rank = rank
    return scored


__all__ = [
    "KeywordRetriever",
    "cjk_ngram_groups",
    "estimate_tokens",
    "expand_cjk_terms",
    "fts_match_expression",
    "rerank_by_frequency",
    "tokenize",
    "validate_query",
]
