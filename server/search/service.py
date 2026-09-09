"""M4 search service: FTS5 primary path + M3 substring fallback (PLAN-M4 §5.4).

Strategy (documented in PLAN-M4 §5.4 and the M4 dev report):

1. **FTS5 main path** — used when every query term is ASCII-tokenizable
   (letters/digits/``-``/``_``/``.``; no CJK/emoji/symbols).  Each term is
   turned into a quoted FTS5 phrase and phrases are space-joined (implicit
   AND).  The FTS5 ``notes_fts`` table indexes title/basename/tags/body with
   ``unicode61``; ranking uses ``bm25`` with column weights
   (title > basename > tags > body).  FTS5 tokenization is inherently
   case-insensitive and splits on punctuation, so FTS rows may be a superset
   of raw-substring rows — intended behaviour of the new main path.
2. **Substring fallback (M3 degraded path)** — used when the FTS path is
   unavailable (no FTS5 in the SQLite build), a term cannot be represented by
   the ASCII-tokenizable FTS path (Chinese short queries, emoji, accented
   text), or the FTS query raised/returned nothing.  It reproduces the M3
   semantics exactly: every whitespace term must appear as a casefolded
   substring inside the note corpus (basename + title + tags + body), with
   basename/title/tag scoring boosts.

The REST contract is unchanged: ``SearchResponse{query,hits,total,degraded,
skipped_notes,generated_at}`` and ``SearchHit{path,title,snippet,
matched_terms,score}``.  ``degraded`` keeps its M3 meaning (some notes were
unindexable); whether a particular query used the substring fallback is an
implementation detail and is not part of the DTO (PLAN-M4 §10.3 open item).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from ..index.schemas import SearchCandidate
from ..index.service import DerivedIndexService
from ..vault.errors import InvalidRequest
from .schemas import SearchHit, SearchResponse

_MAX_QUERY_LENGTH = 256
_MAX_TERM_LENGTH = 64
_SNIPPET_LEFT = 40
_SNIPPET_RIGHT = 80

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# A term may ride the FTS path only when every character is ASCII
# tokenizable (unicode61 output) — CJK/emoji/accents/symbols go substring.
_FTS_SAFE_TERM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _validate_query(query: str) -> None:
    if not isinstance(query, str) or not query.strip():
        raise InvalidRequest("Search query must not be empty")
    if len(query) > _MAX_QUERY_LENGTH:
        raise InvalidRequest("Search query is too long")
    if _CONTROL_RE.search(query):
        raise InvalidRequest("Search query contains control characters")
    for term in query.split():
        if len(term) > _MAX_TERM_LENGTH:
            raise InvalidRequest("Search term is too long")


def _snippet(body: str, terms: list[str]) -> str:
    """Extract a snippet around the first matching term.
    
    P1-4 fix: Safe boundary handling for Chinese text (no word separators).
    Limits the forward scan to prevent index errors in long continuous text.
    """
    if not body:
        return ""
    folded = body.casefold()
    best: tuple[int, str] | None = None
    for term in terms:
        index = folded.find(term)
        if index >= 0 and (best is None or index < best[0]):
            best = (index, term)
    if best is None:
        return ""
    
    index = best[0]
    term_len = len(best[1])
    
    # Safe boundary calculation
    start = max(0, index - _SNIPPET_LEFT)
    end = min(len(body), index + term_len + _SNIPPET_RIGHT)
    
    # Try to find a word boundary, but limit the scan to avoid running off
    # Limit: scan at most 30 characters forward to find a space
    original_start = start
    scan_limit = 30
    steps = 0
    while start > 0 and start < len(body) and steps < scan_limit:
        if body[start] in " \t\n":
            break
        start += 1
        steps += 1
    
    # If we scanned too far and would cut the matched term, reset to original
    if start > index:
        start = original_start
    
    # Ensure boundaries are valid
    start = max(0, min(start, len(body)))
    end = max(start, min(end, len(body)))
    
    # Extract and normalize whitespace
    window = body[start:end]
    window = " ".join(window.split())
    
    # Add ellipses
    if start > 0:
        window = "…" + window
    if end < len(body):
        window = window + "…"
    
    return window


class SearchService:
    def __init__(self, index: DerivedIndexService) -> None:
        self._index = index

    # ------------------------------------------------------------------
    # Public contract
    # ------------------------------------------------------------------

    def search(self, query: str) -> SearchResponse:
        _validate_query(query)
        self._index.assert_ready()
        terms = list(dict.fromkeys(term.casefold() for term in query.split()))
        hits = self._search(terms)
        hits.sort(key=lambda hit: (-hit.score, hit.path.casefold()))
        skipped = self._index.skipped_count + self._index.failed_count
        return SearchResponse(
            query=query,
            hits=hits,
            total=len(hits),
            degraded=skipped > 0,
            skipped_notes=skipped,
            generated_at=datetime.now(UTC),
        )

    # ------------------------------------------------------------------
    # Path selection
    # ------------------------------------------------------------------

    def _search(self, terms: list[str]) -> list[SearchHit]:
        fts_eligible = self._index.fts_available and all(
            _FTS_SAFE_TERM_RE.match(term) for term in terms
        )
        fts_hits: list[SearchHit] = []
        fts_usable = False
        if fts_eligible:
            match_expr = " ".join(f'"{term}"' for term in terms)
            candidates = self._index.fts_search(match_expr)
            fts_usable = True
            fts_hits = [
                self._hit_from_fts(candidate, terms)
                for candidate in candidates
            ]
        if fts_usable and fts_hits:
            # FTS returned rows and the query is fully FTS-representable:
            # bm25 ranking is authoritative (PLAN-M4 §5.4).
            return fts_hits
        # Degraded M3 substring path: FTS unavailable, terms not
        # FTS-representable, the query errored, or FTS found nothing.
        candidates = self._index.substring_search(terms)
        return [
            self._hit_from_substring(candidate, terms)
            for candidate in candidates
        ]

    # ------------------------------------------------------------------
    # Hit shaping
    # ------------------------------------------------------------------

    def _hit_from_fts(self, item: SearchCandidate, terms: list[str]) -> SearchHit:
        # FTS5 bm25 returns non-positive values where *lower* is better;
        # negate so that a higher ``score`` is better (DTO sort is desc).
        raw = float(item.score or 0.0)
        score = round(0.0 - raw, 4)
        return SearchHit(
            path=item.path,
            title=item.title,
            snippet=_snippet(item.text, terms),
            matched_terms=list(terms),
            score=score,
        )

    def _hit_from_substring(self, item: SearchCandidate, terms: list[str]) -> SearchHit:
        stem_folded = item.basename.casefold()
        title_folded = item.title.casefold()
        tag_list = item.tags_folded.split(" ") if item.tags_folded else []
        file_hit = any(term in stem_folded for term in terms)
        title_hit = any(term in title_folded for term in terms)
        tag_hit = any(
            term in tag for term in terms for tag in tag_list
        )
        score = round(
            (4.0 if file_hit else 0.0)
            + (2.0 if title_hit else 0.0)
            + (1.0 if tag_hit else 0.0)
            + 1.0 * len(terms),
            2,
        )
        return SearchHit(
            path=item.path,
            title=item.title,
            snippet=_snippet(item.text, terms),
            matched_terms=list(terms),
            score=score,
        )


__all__ = ["SearchService"]
