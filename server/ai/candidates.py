"""Programmatic related-note candidate reduction from existing read-only indexes.

PLAN-M6 §5.6: candidates are narrowed *before* any model call.  The current
note is excluded, only paths present in the index (or graph snapshot) are ever
accepted (strict allow-list), and every path a later model suggestion may make
must fall inside this returned set.

Sources, in priority order:

0. **M14 RAG hybrid retrieval** (``rag_chunks``) when a RAG stack is available:
   chunk-level FTS + vector evidence fused by RRF, which is the only source that
   can find a semantically similar note that shares no keywords. This is the
   "Related Notes reuses EvidencePack" path (M14 §八) — the same retrieval that
   grounds RAG answers narrows the M6 candidate set, so no note can be proposed
   unless retrieval actually found it in the Vault;
1. FTS5 (``index.fts_search``) when every query term is ASCII-tokenizable;
   otherwise the M3 substring path (``index.substring_search``) — mirroring
   SearchService's FTS-primary / substring-fallback split;
2. resolved link neighbors of the current note (outgoing + backlinks from the
   graph snapshot);
3. M5 read-only graph snapshot neighbors.

Rows are de-duplicated by path, bounded to ``limit`` and returned in source
priority order.  ``limit <= 0`` returns no candidates.
"""

from __future__ import annotations

import re

# Same shape as SearchService's FTS eligibility test (M4 unicode61 tokenizer):
# a term may ride the FTS path only when it is fully ASCII-tokenizable.
_FTS_SAFE_TERM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _path_of(candidate: object) -> object:
    if isinstance(candidate, str):
        return candidate
    return getattr(candidate, "path", None)


def reduce_candidates(
    index,
    path: str,
    *,
    limit: int = 20,
    query: str | None = None,
    rag_hits=None,
):
    """Merge RAG, FTS/substring, link, and graph candidates for one note.

    Only existing read APIs are consumed (``entries``, ``fts_search``,
    ``substring_search``, ``graph_snapshot``, and the optional M14 RAG chunk
    hits). Rows are bounded before any model call and every returned path must
    occur in the index note set, preventing model-selected arbitrary filesystem
    paths — a hallucinated path can never become a candidate.
    """
    if limit <= 0:
        return []
    allowed: dict[str, object] = {}
    try:
        entries = list(index.entries())
    except Exception:
        entries = []
    for row in entries:
        candidate_path = getattr(row, "path", None)
        if isinstance(candidate_path, str) and candidate_path:
            # The current note stays in the map (link/graph sources are read
            # from its row); ``add`` still refuses to return it as a candidate.
            allowed[candidate_path] = row

    order: list[str] = []

    def add(candidate: object) -> None:
        candidate_path = _path_of(candidate)
        if not isinstance(candidate_path, str) or not candidate_path or candidate_path == path:
            return
        if candidate_path not in allowed or candidate_path in order:
            return
        if len(order) >= limit:
            return
        order.append(candidate_path)

    # Highest priority: semantic + lexical evidence from the RAG chunk index
    # (``RetrievalResult.path`` / ``VectorHit.path`` objects).
    for hit in rag_hits or ():
        add(hit)

    terms = query.split() if query else []
    if terms:
        hits: list[object] = []
        if all(_FTS_SAFE_TERM_RE.fullmatch(term) for term in terms):
            try:
                expr = " ".join(f'"{term}"' for term in terms)
                hits = list(index.fts_search(expr))
            except Exception:
                hits = []
        if not hits:
            try:
                hits = list(index.substring_search(terms))
            except Exception:
                hits = []
        for row in hits:
            add(row)

    # Link source from the existing indexed note row of the current note.
    source = allowed.get(path)
    if source is not None:
        for target in getattr(source, "resolved_targets", set()) or ():
            add(target)
        for link in getattr(source, "outgoing", []) or ():
            add(getattr(link, "resolved_path", None))

    # Graph source, if the M5 read-only projection is available.
    try:
        snapshot = index.graph_snapshot()
    except Exception:
        snapshot = None
    if snapshot is not None:
        for row in snapshot.links:
            if row.source_path == path and row.resolved_path:
                add(row.resolved_path)
            elif row.resolved_path == path and row.source_path:
                add(row.source_path)

    return [allowed[item] for item in order[:limit]]
