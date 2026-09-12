"""Link / graph weighted retrieval (M14 §六 — roadmap item ③).

The lexical and semantic paths answer "which chunks *talk about* the query".
This retriever answers a different question: "which notes are *connected to*
the notes the query already found" — wikilink out-edges, backlink in-edges,
notes sharing a tag, and notes two hops away in the derived link graph.

Everything here is **read-only derived data**:

- the note graph comes from ``DerivedIndexService.graph_snapshot()`` (one
  coherent read of ``notes``/``tags``/``links``), never from the Vault;
- the chunk text of a candidate comes from the RAG chunk tables (or, when the
  RAG index has no chunk for that path, from the M4 index text);
- nothing is ever written to SQLite or the Vault — deleting ``.localnote/``
  still loses nothing that the Markdown files cannot rebuild.

Honesty rules this module enforces (they are what make the link path usable as
evidence):

- a candidate that is not a row of the derived index is dropped, so a broken
  ``[[missing]]`` link can never surface as a citation;
- the current note (``exclude_paths``) is never returned;
- a **chunk-level** hit carries the real ``chunk_id``/line range/content hash of
  an indexed chunk; a **document-level** hit (``document_level=True``) carries
  the ``doc::<path>`` pseudo id and deliberately keeps the default 1/1 line
  range instead of inventing a span — callers must not present it as
  line-accurate;
- the output order is a pure function of the seed set: scores are weighted
  counts of distinct seeds per signal, and ties break by ``path``, so the same
  index always yields the same order.

Degradation is reported, never thrown at the caller: :attr:`LinkRetriever.last_degraded`
is ``"link_unavailable"`` when the graph cannot be read at all and
``"link_no_seed"`` when the query produced no lexical anchor; the hybrid
retriever copies that marker into ``stats.degraded``. An empty *neighbour set*
is a normal outcome, not a degradation.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from ...index.errors import IndexUnavailable
from ...index.service import DerivedIndexService
from ..schemas import RetrievalResult, content_hash
from ..vector.sqlite import RagStoreUnavailable, SqliteVectorStore
from .base import BaseRetriever, Retriever, RetrieverUnavailable
from .keyword import KeywordRetriever, tokenize

logger = logging.getLogger("localnote.rag.retrieval.link")

# Signal names. They are also the keys of ``LinkRetriever.last_signals`` so a
# debug view can explain *why* a path was returned.
SIGNAL_WIKILINK = "wikilink"
SIGNAL_BACKLINK = "backlink"
SIGNAL_TAG = "tag"
SIGNAL_GRAPH = "graph"
SIGNALS = (SIGNAL_WIKILINK, SIGNAL_BACKLINK, SIGNAL_TAG, SIGNAL_GRAPH)

# Default weights, kept in sync with ``RagSettings`` (link_retrieval_enabled /
# wikilink_weight / ...). They are constructor parameters, never module-level
# thresholds consulted by the code: tests and the settings API inject their own.
DEFAULT_TOP_K = 20
DEFAULT_SEED_TOP_K = 5
DEFAULT_WIKILINK_WEIGHT = 1.0
DEFAULT_BACKLINK_WEIGHT = 0.8
DEFAULT_TAG_WEIGHT = 0.6
DEFAULT_GRAPH_WEIGHT = 0.4
# A very popular tag must not turn one seed into the whole Vault; 0 disables the
# cap (the value is injectable, so no policy is hardcoded in the algorithm).
DEFAULT_MAX_TAG_NEIGHBOURS = 50
# Character cap for a document-level excerpt (mirrors KeywordRetriever's
# document fallback, which uses the same order of magnitude).
DEFAULT_DOCUMENT_CHAR_LIMIT = 2000


@dataclass(slots=True)
class _GraphView:
    """Immutable adjacency/tag view built from one graph snapshot."""

    notes: frozenset[str]
    outgoing: dict[str, tuple[str, ...]]
    incoming: dict[str, tuple[str, ...]]
    tags: dict[str, tuple[str, ...]]
    tag_index: dict[str, tuple[str, ...]]


def _build_graph_view(snapshot) -> _GraphView:
    """Turn one ``GraphSnapshot`` into deterministic adjacency maps.

    Only *resolved, existing* targets become edges: an unresolved wikilink
    (``broken``/``resolved_path is None``) and a target that has no ``notes``
    row are dropped, which is the first place the "never return a path that does
    not exist" rule is enforced. Duplicate edges between the same pair are
    collapsed (``links`` keeps one row per occurrence in document order).
    """
    notes = frozenset(row.path for row in snapshot.notes)
    outgoing: dict[str, list[str]] = {}
    incoming: dict[str, list[str]] = {}
    seen_edges: set[tuple[str, str]] = set()
    for link in snapshot.links:
        source = str(link.source_path)
        target = link.resolved_path
        if not target or link.broken:
            continue
        target = str(target)
        if source == target or target not in notes:
            continue
        edge = (source, target)
        if edge in seen_edges:
            continue
        seen_edges.add(edge)
        outgoing.setdefault(source, []).append(target)
        incoming.setdefault(target, []).append(source)
    tags: dict[str, list[str]] = {}
    tag_index: dict[str, list[str]] = {}
    for row in snapshot.tags:
        note_path = str(row.note_path)
        folded = str(row.tag_folded)
        bucket = tags.setdefault(note_path, [])
        if folded not in bucket:
            bucket.append(folded)
        index_bucket = tag_index.setdefault(folded, [])
        if note_path not in index_bucket:
            index_bucket.append(note_path)
    return _GraphView(
        notes=notes,
        outgoing={key: tuple(value) for key, value in outgoing.items()},
        incoming={key: tuple(value) for key, value in incoming.items()},
        tags={key: tuple(value) for key, value in tags.items()},
        tag_index={key: tuple(value) for key, value in tag_index.items()},
    )


class LinkRetriever(BaseRetriever):
    """Weighted wikilink / backlink / tag / graph-neighbour candidates."""

    name = "link"

    def __init__(
        self,
        store: SqliteVectorStore,
        *,
        index: DerivedIndexService | None = None,
        seed_retriever: Retriever | None = None,
        top_k: int = DEFAULT_TOP_K,
        seed_top_k: int = DEFAULT_SEED_TOP_K,
        wikilink_weight: float = DEFAULT_WIKILINK_WEIGHT,
        backlink_weight: float = DEFAULT_BACKLINK_WEIGHT,
        tag_weight: float = DEFAULT_TAG_WEIGHT,
        graph_weight: float = DEFAULT_GRAPH_WEIGHT,
        max_tag_neighbours: int = DEFAULT_MAX_TAG_NEIGHBOURS,
        document_char_limit: int = DEFAULT_DOCUMENT_CHAR_LIMIT,
        exclude_paths: Sequence[str] | None = None,
    ) -> None:
        self._store = store
        self._index = index
        # The seeds are the notes the query itself matched; KeywordRetriever is
        # the cheapest deterministic anchor source and reuses the same derived
        # FTS/substring ladder the keyword path already documents.
        self._seed_retriever: Retriever = seed_retriever or KeywordRetriever(
            store, index=index
        )
        self.top_k = max(1, int(top_k))
        self.seed_top_k = max(1, int(seed_top_k))
        self.wikilink_weight = max(0.0, float(wikilink_weight))
        self.backlink_weight = max(0.0, float(backlink_weight))
        self.tag_weight = max(0.0, float(tag_weight))
        self.graph_weight = max(0.0, float(graph_weight))
        self.max_tag_neighbours = max(0, int(max_tag_neighbours))
        self.document_char_limit = max(0, int(document_char_limit))
        self.exclude_paths = tuple(
            sorted({str(path) for path in (exclude_paths or ()) if str(path)})
        )
        # Diagnostics (read by HybridRetriever / ``?debug=1``).
        self.last_degraded: str | None = None
        self.last_seed_paths: list[str] = []
        self.last_signals: dict[str, dict[str, int]] = {}

    # ------------------------------------------------------------------

    @property
    def weights(self) -> dict[str, float]:
        return {
            SIGNAL_WIKILINK: self.wikilink_weight,
            SIGNAL_BACKLINK: self.backlink_weight,
            SIGNAL_TAG: self.tag_weight,
            SIGNAL_GRAPH: self.graph_weight,
        }

    @property
    def link_retrieval(self) -> str:
        """Static reachability of the graph read: ``"enabled"`` or a reason.

        Computed from the configured index *without* running a query, so a fresh
        process reports the truth on its first access. ``last_degraded`` is a
        per-``retrieve()`` value (``None`` until the first call) and is
        deliberately not consulted here — reading it would report an unreachable
        graph as enabled. Per-query conditions such as ``link_no_seed`` therefore
        stay in ``last_degraded``/``stats.degraded`` and are not part of this
        status.
        """
        index = self._index
        if index is None:
            return "link_unavailable"
        # ``build_state == "unavailable"`` is the derived index telling us it
        # cannot be opened/read. "idle" is a healthy never-rebuilt index (its
        # reads still work), so it must NOT be treated as a failure.
        if getattr(index, "build_state", None) == "unavailable":
            return "link_unavailable"
        return "enabled"

    def retrieve(
        self,
        query: str,
        *,
        top_k: int | None = None,
        query_vector: Sequence[float] | None = None,
        allowed_paths: Sequence[str] | None = None,
    ) -> list[RetrievalResult]:
        """Return link-expanded candidates for ``query`` (never raises on data).

        ``query_vector`` is part of the :class:`Retriever` protocol and is
        ignored here: this path is graph-driven, not embedding-driven.
        ``top_k=None`` uses the configured ``link_top_k`` (``self.top_k``).
        """
        del query_vector  # protocol parameter; the link path is lexical+graph
        self.last_degraded = None
        self.last_seed_paths = []
        self.last_signals = {}

        text = str(query or "").strip()
        if not text:
            return []
        limit = max(0, int(self.top_k if top_k is None else top_k))
        if limit <= 0:
            return []
        if self._index is None:
            # No derived graph to read: the caller records "link_unavailable".
            self.last_degraded = "link_unavailable"
            return []

        allowed = set(allowed_paths) if allowed_paths is not None else None
        seeds = self._seeds(text)
        self.last_seed_paths = list(seeds)
        if not seeds:
            self.last_degraded = "link_no_seed"
            return []

        view = self._graph_view()
        connections = self._connections(seeds, view, allowed)
        if not connections:
            return []

        weights = self.weights
        ordered: list[tuple[float, str, str]] = []
        for path, signals in connections.items():
            total = sum(weights[signal] * len(items) for signal, items in signals.items())
            if total <= 0.0:
                continue  # every contributing weight is configured to 0
            ordered.append((total, path, self._source_of(signals)))
        # Deterministic: score desc, then path asc. Independent of seed order.
        ordered.sort(key=lambda item: (-item[0], item[1]))

        terms = tokenize(text)
        results: list[RetrievalResult] = []
        for score, path, source in ordered:
            hit = self._materialise(path, terms=terms, source=source)
            if hit is None:
                continue
            hit.score = round(float(score), 6)
            results.append(hit)
        results = results[:limit]
        for rank, hit in enumerate(results, start=1):
            hit.link_rank = rank
        self.last_signals = {
            hit.path: {
                signal: len(connections.get(hit.path, {}).get(signal, ()))
                for signal in SIGNALS
            }
            for hit in results
        }
        return results

    # ------------------------------------------------------------------
    # Seeds
    # ------------------------------------------------------------------

    def _seeds(self, query: str) -> list[str]:
        """Distinct paths the query itself matched (best first, de-duplicated).

        Seeds are *anchors*, not results: they are chosen purely by relevance to
        the query and are never returned (the lexical/semantic paths already own
        them). The caller's ``allowed_paths`` therefore constrains what may come
        *out* of this retriever, not which note may be used to walk the graph.
        """
        try:
            hits = self._seed_retriever.retrieve(query, top_k=self.seed_top_k)
        except RetrieverUnavailable as exc:
            raise RetrieverUnavailable(f"link seeds unavailable: {exc}") from exc
        seeds: list[str] = []
        for hit in hits:
            path = str(hit.path)
            if path in seeds:
                continue
            if path in self.exclude_paths:
                continue
            seeds.append(path)
            if len(seeds) >= self.seed_top_k:
                break
        return seeds

    # ------------------------------------------------------------------
    # Graph expansion
    # ------------------------------------------------------------------

    def _graph_view(self) -> _GraphView:
        assert self._index is not None
        try:
            snapshot = self._index.graph_snapshot()
        except IndexUnavailable as exc:
            # "Cannot run right now" (index missing/closed): the caller degrades
            # to the other paths and records the reason.
            raise RetrieverUnavailable("derived link graph is unavailable") from exc
        return _build_graph_view(snapshot)

    def _connections(
        self,
        seeds: Sequence[str],
        view: _GraphView,
        allowed: set[str] | None,
    ) -> dict[str, dict[str, set[str]]]:
        """path -> {signal -> set(seed)} for every reachable candidate."""
        seed_set = set(seeds)
        connections: dict[str, dict[str, set[str]]] = {}

        def connect(path: str, signal: str, seed: str) -> None:
            if path == seed or path not in view.notes:
                return
            if path in self.exclude_paths:
                return
            if allowed is not None and path not in allowed:
                return
            if path in seed_set:
                # An anchor is not an expansion: it is already in the direct
                # (keyword) pool, so returning it here would only duplicate it.
                return
            connections.setdefault(path, {}).setdefault(signal, set()).add(seed)

        for seed in seeds:
            for target in view.outgoing.get(seed, ()):
                connect(target, SIGNAL_WIKILINK, seed)
            for source in view.incoming.get(seed, ()):
                connect(source, SIGNAL_BACKLINK, seed)
            for tag in view.tags.get(seed, ()):
                neighbours = view.tag_index.get(tag, ())
                if self.max_tag_neighbours:
                    # The cap is a per-tag fan-out limit on *candidate* notes, so
                    # the anchors themselves never consume a slot.
                    neighbours = tuple(
                        path for path in neighbours if path not in seed_set
                    )[: self.max_tag_neighbours]
                for other in neighbours:
                    connect(other, SIGNAL_TAG, seed)

        if self.graph_weight > 0.0:
            for seed in seeds:
                one_hop = set(view.outgoing.get(seed, ())) | set(
                    view.incoming.get(seed, ())
                )
                one_hop.discard(seed)
                # Exactly-two-hops neighbours: a note that is already a direct
                # neighbour is scored by the wikilink/backlink signal instead of
                # being counted twice.
                for hop in sorted(one_hop):
                    for path in (
                        *view.outgoing.get(hop, ()),
                        *view.incoming.get(hop, ()),
                    ):
                        if path in one_hop or path == seed:
                            continue
                        connect(path, SIGNAL_GRAPH, seed)
        return connections

    @staticmethod
    def _source_of(signals: dict[str, set[str]]) -> str:
        """``graph`` only when the two-hop signal is the only explanation."""
        direct = {SIGNAL_WIKILINK, SIGNAL_BACKLINK, SIGNAL_TAG}
        if SIGNAL_GRAPH in signals and not (direct & set(signals)):
            return "graph"
        return "link"

    # ------------------------------------------------------------------
    # Materialisation
    # ------------------------------------------------------------------

    def _materialise(
        self, path: str, *, terms: Sequence[str], source: str
    ) -> RetrievalResult | None:
        chunk = self._best_chunk(path, terms)
        if chunk is not None:
            return RetrievalResult(
                chunk_id=chunk.chunk_id,
                path=chunk.path,
                heading=chunk.heading,
                heading_path=chunk.section_path or chunk.heading_path,
                content=chunk.content,
                score=0.0,
                link_rank=None,  # assigned by the caller once the order is final
                start_line=chunk.start_line,
                end_line=chunk.end_line,
                tags=list(chunk.tags),
                source=source,  # type: ignore[arg-type]
                content_hash=chunk.content_hash,
            )
        entry = self._entry(path)
        if entry is None:
            # Not a derived index row (or the index cannot be read): dropping it
            # is what keeps "link" evidence from pointing at a path that does
            # not exist.
            return None
        text = str(entry.text or "")
        content = text[: self.document_char_limit] if self.document_char_limit else text
        return RetrievalResult(
            chunk_id=f"doc::{path}",
            path=path,
            heading=entry.title or None,
            heading_path=entry.title or None,
            content=content,
            score=0.0,
            link_rank=None,
            # No line-accurate span is known for a whole-document hit, so the
            # dataclass defaults are kept and the hit is flagged instead of
            # inventing a range.
            start_line=1,
            end_line=1,
            tags=list(entry.tags or []),
            source=source,  # type: ignore[arg-type]
            content_hash=content_hash(content),
            document_level=True,
        )

    def _best_chunk(self, path: str, terms: Sequence[str]):
        """Chunk of ``path`` that best covers the query (document order wins ties)."""
        try:
            chunks = self._store.chunks_for_document(path)
        except RagStoreUnavailable:
            return None
        except Exception:  # pragma: no cover - defensive, store stays optional
            logger.debug("chunk lookup failed for %s", path, exc_info=True)
            return None
        if not chunks:
            return None
        best = chunks[0]
        best_score = -1
        for chunk in chunks:
            haystack = chunk.content.casefold()
            heading = (chunk.heading or "").casefold()
            score = 2 * sum(1 for term in terms if term and term in haystack)
            score += sum(1 for term in terms if term and term in heading)
            if score > best_score:  # strict: first chunk_index wins a tie
                best, best_score = chunk, score
        return best

    def _entry(self, path: str):
        assert self._index is not None
        try:
            return self._index.entry(path)
        except IndexUnavailable:
            return None
        except Exception:  # pragma: no cover - defensive
            logger.debug("index entry lookup failed for %s", path, exc_info=True)
            return None


__all__ = [
    "DEFAULT_BACKLINK_WEIGHT",
    "DEFAULT_DOCUMENT_CHAR_LIMIT",
    "DEFAULT_GRAPH_WEIGHT",
    "DEFAULT_MAX_TAG_NEIGHBOURS",
    "DEFAULT_SEED_TOP_K",
    "DEFAULT_TAG_WEIGHT",
    "DEFAULT_TOP_K",
    "DEFAULT_WIKILINK_WEIGHT",
    "LinkRetriever",
    "SIGNALS",
    "SIGNAL_BACKLINK",
    "SIGNAL_GRAPH",
    "SIGNAL_TAG",
    "SIGNAL_WIKILINK",
]
