"""M5 read-only graph projection service (PLAN-M5 §3.3/§3.4/§5.2–§5.4).

The graph is **computed at query time** from one coherent
:class:`~server.index.schemas.GraphSnapshot` (notes/tags/links rows read
under the index RLock by ``DerivedIndexService.graph_snapshot``); no graph
tables, no extra migrations, and ``server/graph`` never executes
INSERT/UPDATE/DELETE.  ``GraphService`` is stateless besides its index
reference and settings snapshot; a response DTO can always be discarded and
recomputed.

Node/edge rules implemented here (fixed contract for M5, mirrored in
``packages/protocol`` and covered by the backend/frontend matrices):

- Two node kinds only: ``note`` (M4 ``notes`` row) and ``tag`` (M4 ``tags``
  casefold key).  Headings/blocks stay link metadata; attachments and web
  links never become nodes.
- Stable IDs: ``note:<pct(path)>``, ``tag:<pct(folded)>``,
  ``link:<pct(source)>#<seq>``, ``tag:<pct(note)>#<pct(folded)>`` where
  ``pct`` percent-encodes every UTF-8 byte.  ``link`` IDs embed the M4
  ``seq`` so duplicated links from one source stay distinct.
- Deterministic ordering: nodes ``(type, id)``, edges
  ``(type, source, target, id)``; candidates/pages are sliced **after**
  sorting.
- Broken refs keep their source edge with ``broken=true`` and an empty
  ``target`` (no fabricated node).  Ambiguous refs keep M4 ``candidates``
  (sorted by the index); a determined resolved *note* target is emitted with
  ``ambiguous=true``, otherwise the edge is dangling (empty target).
- Backlinks are *not* emitted as separate wire edges: they exist only as the
  incoming traversal used by the local scope BFS (PLAN-M5 §3.3 keeps the
  ``backlink`` value reserved on ``GraphEdgeType``).
"""

from __future__ import annotations

import logging
import math
import struct
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from ..config import GraphSettings
from ..index.schemas import GraphSnapshot
from ..index.service import DerivedIndexService
from ..rag.vector.sqlite import SqliteVectorStore
from ..vault.errors import InvalidRequest, PathNotFound
from .schemas import GraphEdge, GraphNode, GraphPage, GraphResponse

logger = logging.getLogger("localnote.graph")

_DIRECTIONS: frozenset[str] = frozenset({"both", "outgoing", "incoming"})
_MAX_INPUT_LENGTH = 2048
_NOTE_ID_PREFIX = "note:"
_TAG_ID_PREFIX = "tag:"
_LINK_EDGE_PREFIX = "link:"
_TAG_EDGE_PREFIX = "tag:"


# ---------------------------------------------------------------------------
# Stable ID helpers (byte-exact percent encoding, no decoding ever needed:
# node IDs are opaque to every consumer).
# ---------------------------------------------------------------------------


def _pct(value: str) -> str:
    return "".join(f"%{byte:02X}" for byte in value.encode("utf-8"))


def note_id(path: str) -> str:
    return _NOTE_ID_PREFIX + _pct(path)


def tag_id(tag_folded: str) -> str:
    return _TAG_ID_PREFIX + _pct(tag_folded)


def link_edge_id(source_path: str, seq: int) -> str:
    return _LINK_EDGE_PREFIX + _pct(source_path) + "#" + str(seq)


def tag_edge_id(note_path: str, tag_folded: str) -> str:
    return _TAG_EDGE_PREFIX + _pct(note_path) + "#" + _pct(tag_folded)


def _tag_display(tag: str) -> tuple[str, str]:
    """Deterministic display: minimum of ``(casefold, display)`` tuples."""
    key = tag.casefold()
    return key, tag


# ---------------------------------------------------------------------------
# Snapshot-derived maps
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _GraphMaps:
    """Python-side projection of one snapshot (no sqlite rows escape)."""

    note_titles: dict[str, str]  # path -> derived title
    note_basenames: dict[str, str]  # path -> basename
    tags_by_note: dict[str, list[tuple[str, str]]]  # path -> [(tag, folded)]
    tag_displays: dict[str, str]  # folded -> deterministic display


def _build_maps(snapshot: GraphSnapshot) -> _GraphMaps:
    note_titles: dict[str, str] = {}
    note_basenames: dict[str, str] = {}
    for note in snapshot.notes:
        note_titles[note.path] = note.title
        note_basenames[note.path] = note.basename
    tags_by_note: dict[str, list[tuple[str, str]]] = {}
    displays_by_folded: dict[str, list[str]] = {}
    for tag in snapshot.tags:
        tags_by_note.setdefault(tag.note_path, []).append((tag.tag, tag.tag_folded))
        displays_by_folded.setdefault(tag.tag_folded, []).append(tag.tag)
    tag_displays = {
        folded: min(displays, key=_tag_display) for folded, displays in displays_by_folded.items()
    }
    return _GraphMaps(
        note_titles=note_titles,
        note_basenames=note_basenames,
        tags_by_note=tags_by_note,
        tag_displays=tag_displays,
    )


def _note_has_tag(maps: _GraphMaps, path: str, folded: str) -> bool:
    return any(item[1] == folded for item in maps.tags_by_note.get(path, ()))


def _note_adjacency(
    snapshot: GraphSnapshot,
    notes: set[str],
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Forward/backward note-neighbour maps for the local BFS.

    ``forward[note]`` = resolved *note* targets of its outgoing rows;
    ``backward[note]`` = notes whose rows resolve to ``note`` (the M4
    backlinks semantics, derived from the same links rows so the snapshot is
    single-source).  Self loops never expand the frontier.  Rows that only
    resolve to attachments/web targets contribute nothing.
    """
    forward: dict[str, set[str]] = {}
    backward: dict[str, set[str]] = {}
    for row in snapshot.links:
        if row.kind == "web" or row.resolved_path is None:
            continue
        source = row.source_path
        target = row.resolved_path
        if source == target or source not in notes or target not in notes:
            continue
        forward.setdefault(source, set()).add(target)
        backward.setdefault(target, set()).add(source)
    return forward, backward


# ---------------------------------------------------------------------------
# Scope assembly: nodes + edges for one note set
# ---------------------------------------------------------------------------


def _scope_nodes_and_edges(
    maps: _GraphMaps,
    snapshot: GraphSnapshot,
    *,
    note_paths: set[str],
    folded_filter: str | None,
    include_broken: bool,
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Assemble the deterministic node/edge candidate lists of one scope.

    ``folded_filter`` restricts tag nodes/edges to that one casefolded tag
    (used by tag scope and by every ``tag=`` filter); ``None`` means every
    tag present on the included notes participates.  Only edges whose
    endpoints are real nodes of the scope are produced here — never a normal
    edge referencing a missing node.
    """
    # Nodes: included notes always exist in the snapshot; tag nodes are only
    # created for folded keys actually present on an included note.
    tag_foldeds: set[str] = set()
    for path in note_paths:
        for _, folded in maps.tags_by_note.get(path, ()):
            tag_foldeds.add(folded)
    if folded_filter is not None:
        tag_foldeds &= {folded_filter}

    nodes: list[GraphNode] = []
    for path in sorted(note_paths, key=str.casefold):
        title = maps.note_titles.get(path, "") or ""
        basename = maps.note_basenames.get(path, "") or ""
        nodes.append(
            GraphNode(
                id=note_id(path),
                type="note",
                label=title or basename or path,
                path=path,
                title=title or None,
            )
        )
    for folded in sorted(tag_foldeds, key=str.casefold):
        display = maps.tag_displays.get(folded, folded)
        nodes.append(
            GraphNode(
                id=tag_id(folded),
                type="tag",
                label=display,
                tag=display,
                tag_folded=folded,
            )
        )
    nodes.sort(key=lambda node: (node.type, node.id))

    edges: list[GraphEdge] = []
    # tag edges: note -> tag, in note/seq order of the tags table
    for path in sorted(note_paths, key=str.casefold):
        for _tag, folded in maps.tags_by_note.get(path, ()):
            if folded not in tag_foldeds:
                continue
            edges.append(
                GraphEdge(
                    id=tag_edge_id(path, folded),
                    source=note_id(path),
                    target=tag_id(folded),
                    type="tag",
                    directed=True,
                    raw=None,
                )
            )
    # link edges (kind != web): resolved note targets become normal edges;
    # broken rows and ambiguous rows without a note target become dangling
    # edges that are excluded when ``include_broken`` is False.
    for row in snapshot.links:
        if row.source_path not in note_paths or row.kind == "web":
            continue
        resolved = row.resolved_path
        if resolved is not None and resolved in note_paths:
            edges.append(
                GraphEdge(
                    id=link_edge_id(row.source_path, row.seq),
                    source=note_id(row.source_path),
                    target=note_id(resolved),
                    type="link",
                    directed=True,
                    raw=row.raw,
                    resolved_path=resolved,
                    section=row.section,
                    block=row.block,
                    broken=False,
                    ambiguous=row.ambiguous,
                    candidates=list(row.candidates) if row.ambiguous else [],
                    context=row.context,
                )
            )
        elif row.broken:
            if include_broken:
                edges.append(
                    GraphEdge(
                        id=link_edge_id(row.source_path, row.seq),
                        source=note_id(row.source_path),
                        target="",
                        type="link",
                        directed=True,
                        raw=row.raw,
                        resolved_path=None,
                        section=row.section,
                        block=row.block,
                        broken=True,
                        ambiguous=False,
                        candidates=[],
                        context=row.context,
                    )
                )
        elif row.ambiguous:
            # Ambiguity whose deterministic M4 target is not a note (e.g. the
            # first candidate is an attachment): keep candidates, render as a
            # dangling edge per PLAN-M5 §5.2.
            if include_broken:
                edges.append(
                    GraphEdge(
                        id=link_edge_id(row.source_path, row.seq),
                        source=note_id(row.source_path),
                        target="",
                        type="link",
                        directed=True,
                        raw=row.raw,
                        resolved_path=None,
                        section=row.section,
                        block=row.block,
                        broken=False,
                        ambiguous=True,
                        candidates=list(row.candidates),
                        context=row.context,
                    )
                )
        # else: resolved to a non-note (attachment/embed) — no graph node
        # exists, so this link is not part of the note-tag graph.
    edges.sort(key=lambda edge: (edge.type, edge.source, edge.target, edge.id))
    return nodes, edges


def _page_response(
    *,
    scope: Literal["global", "local", "tag"],
    root: str | None,
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    limit: int,
    offset: int,
    max_edges: int,
) -> GraphResponse:
    """Slice nodes by ``(offset, limit)`` and keep only adjacent edges.

    Pagination always applies to the sorted node candidate list; ``edges``
    already contains every scope edge whose endpoints are scope nodes.  A
    truncated page (more nodes remain, or the edge list would exceed
    ``max_edges``) drops edges that would reference nodes outside the page so
    the payload never contains a normal edge to a missing node (PLAN-M5
    §5.2).  ``page.truncated`` reports either condition.
    """
    edges.sort(key=lambda edge: (edge.type, edge.source, edge.target, edge.id))
    total_nodes = len(nodes)
    total_edges = len(edges)
    page_nodes = nodes[offset : offset + limit]
    next_offset = offset + limit if offset + limit < total_nodes else None
    page_ids = {node.id for node in page_nodes}
    page_edges = [
        edge
        for edge in edges
        if edge.source in page_ids and (not edge.target or edge.target in page_ids)
    ]
    edge_truncated = len(page_edges) > max_edges
    emitted_edges = page_edges[:max_edges]
    truncated = next_offset is not None or edge_truncated
    return GraphResponse(
        model="note-tag-v1",
        scope=scope,
        root=root,
        nodes=page_nodes,
        edges=emitted_edges,
        page=GraphPage(
            limit=limit,
            offset=offset,
            next_offset=next_offset,
            total_nodes=total_nodes,
            total_edges=total_edges,
            truncated=truncated,
        ),
        generated_at=datetime.now(UTC),
    )


def _invalid(message: str) -> InvalidRequest:
    return InvalidRequest(message)


def _validated_limit(value: int | None, settings: GraphSettings) -> int:
    limit = settings.default_limit if value is None else value
    if limit < 1 or limit > settings.max_limit:
        raise _invalid(f"limit must be between 1 and {settings.max_limit}")
    return limit


def _validated_offset(value: int) -> int:
    if value < 0:
        raise _invalid("offset must be >= 0")
    return value


def _validated_depth(value: int, settings: GraphSettings) -> int:
    if value < 0 or value > settings.max_depth:
        raise _invalid(f"depth must be between 0 and {settings.max_depth}")
    return value


def _validated_direction(value: str) -> str:
    if value not in _DIRECTIONS:
        raise _invalid("direction must be one of both|outgoing|incoming")
    return value


def _validated_input_text(value: str, label: str) -> str:
    """Reject empty/control/over-long request text (PLAN-M5 §5.3 → 400)."""
    if not value:
        raise _invalid(f"{label} must not be empty")
    if len(value) > _MAX_INPUT_LENGTH:
        raise _invalid(f"{label} is too long")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise _invalid(f"{label} contains control characters")
    return value


def _validated_tag_filter(value: str | None) -> str | None:
    if value is None:
        return None
    tag = _validated_input_text(value, "tag")
    return tag.casefold()


class GraphService:
    """Query-time, read-only note/tag graph over one derived index."""

    def __init__(
        self,
        index: DerivedIndexService,
        *,
        settings: GraphSettings | None = None,
        vector_store: SqliteVectorStore | None = None,
        embedding_provider=None,
        embedding_version: str = "",
    ) -> None:
        self._index = index
        self._settings = settings or GraphSettings()
        self._vector_store = vector_store
        self._embedding_provider = embedding_provider
        self._embedding_version = embedding_version
        self._semantic_lock = threading.RLock()
        self._semantic_cache: dict[tuple, dict[str, list[tuple[str, float]]]] = {}

    def _semantic_projection(
        self, paths: set[str], *, include: bool
    ) -> tuple[list[GraphEdge], str, int]:
        if not include or self._vector_store is None or self._embedding_provider is None:
            return [], "unavailable", 0
        if len(paths) > 500:
            return [], "limited", 0
        provider = self._embedding_provider
        if (
            bool(getattr(provider, "is_degraded", False))
            or "hash" in type(provider).__name__.lower()
        ):
            return [], "degraded", 0
        try:
            state = self._vector_store.state()
            model = str(getattr(provider, "model", ""))
            configured_dimension = int(getattr(provider, "dimension", 0) or 0)
            dimension = configured_dimension or int(state.embedding_dimension or 0)
            version = self._embedding_version
            if (
                state.status not in {"ready", "pending", "failed"}
                or not state.chunk_count
                or not model
                or not dimension
            ):
                return [], "outdated", 0
            if state.embedding_provider != type(provider).__name__:
                return [], "outdated", 0
            generation = self._vector_store.generation
            if (
                state.embedding_model != model
                or state.embedding_dimension != dimension
                or state.embedding_version != version
                or (configured_dimension > 0 and state.embedding_dimension != configured_dimension)
            ):
                return [], "outdated", 0
            entries = self._index.note_fingerprints(paths)
            if not entries:
                return [], "outdated", 0
            key = (
                generation,
                model,
                dimension,
                version,
                tuple(sorted((p, entries[p]) for p in paths)),
            )
            with self._semantic_lock:
                neighbors = self._semantic_cache.get(key)
            if neighbors is None:
                if not self._vector_store.semantic_embeddings_compatible(
                    model=model, dimension=dimension, version=version
                ):
                    return [], "outdated", 0
                rows = self._vector_store.semantic_note_vectors(
                    sorted(paths), model=model, dimension=dimension, version=version
                )
                chunks: dict[str, list[tuple[float, ...]]] = {}
                doc_meta: dict[str, tuple[str, int]] = {}
                invalid_paths: set[str] = set()
                for row in rows:
                    path = str(row["path"])
                    rowmodel = str(row["model"])
                    rowversion = str(row["embedding_version"])
                    rowdim = int(row["dimension"])
                    embedded = int(row["embedded_count"])
                    packed = bytes(row["vector"])
                    vectors = (
                        list(struct.iter_unpack("<" + "f" * rowdim, packed))
                        if rowdim > 0 and embedded > 0 and len(packed) == embedded * rowdim * 4
                        else []
                    )
                    if (
                        rowmodel != model
                        or rowversion != version
                        or rowdim != dimension
                        or not vectors
                        or int(row["chunk_count"]) != embedded
                    ):
                        invalid_paths.add(path)
                        continue
                    chunks.setdefault(path, []).extend(vectors)
                    doc_meta[path] = (str(row["sha256"]), embedded)
                valid_paths = {
                    path
                    for path, vectors in chunks.items()
                    if path in entries
                    and path not in invalid_paths
                    and doc_meta.get(path, (None, 0))[0] == entries[path]
                    and doc_meta.get(path, (None, 0))[1] == len(vectors)
                }
                chunks = {path: vectors for path, vectors in chunks.items() if path in valid_paths}
                if self._vector_store.generation != generation:
                    return [], "outdated", 0
                centroids: dict[str, tuple[float, ...]] = {}
                for path, vectors in chunks.items():
                    mean = [
                        math.fsum(v[i] for v in vectors) / len(vectors) for i in range(dimension)
                    ]
                    norm = math.sqrt(math.fsum(x * x for x in mean))
                    if norm:
                        centroids[path] = tuple(x / norm for x in mean)
                paths_ordered = sorted(centroids)
                top: dict[str, list[tuple[str, float]]] = {
                    path: [] for path in paths_ordered
                }
                # Each pair has the same cosine score in both directions.
                # Score it once in the Python 3.12 C kernel. A generator of
                # Python multiplications makes the first graph load much slower.
                for position, source in enumerate(paths_ordered):
                    vector = centroids[source]
                    for target in paths_ordered[position + 1 :]:
                        score = math.sumprod(vector, centroids[target])
                        if score >= 0.84:
                            top[source].append((target, score))
                            top[target].append((source, score))
                neighbors = {
                    source: sorted(ranked, key=lambda item: (-item[1], item[0]))[:3]
                    for source, ranked in top.items()
                }
                with self._semantic_lock:
                    self._semantic_cache = {key: neighbors}
            edges = []
            for source, candidates in neighbors.items():
                for target, score in candidates:
                    if source >= target or not any(
                        n == source for n, _ in neighbors.get(target, ())
                    ):
                        continue
                    edges.append(
                        GraphEdge(
                            id="semantic:"
                            + _pct(min(source, target))
                            + "#"
                            + _pct(max(source, target)),
                            source=note_id(source),
                            target=note_id(target),
                            type="semantic",
                            directed=False,
                            score=round(score, 6),
                        )
                    )
            return edges, "ready", len(neighbors)
        except Exception:
            logger.exception("semantic graph projection unavailable")
            return [], "degraded", 0

    # ------------------------------------------------------------------
    # Global scope (PLAN-M5 §5.3)
    # ------------------------------------------------------------------

    def global_graph(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
        tag: str | None = None,
        include_broken: bool = True,
        include_semantic: bool = True,
    ) -> GraphResponse:
        self._index.assert_ready()
        bound_limit = _validated_limit(limit, self._settings)
        bound_offset = _validated_offset(offset)
        folded = _validated_tag_filter(tag)
        snapshot = self._index.graph_snapshot()
        maps = _build_maps(snapshot)
        note_paths = {note.path for note in snapshot.notes}
        if folded is not None:
            note_paths = {path for path in note_paths if _note_has_tag(maps, path, folded)}
        nodes, edges = _scope_nodes_and_edges(
            maps,
            snapshot,
            note_paths=note_paths,
            folded_filter=folded,
            include_broken=include_broken,
        )
        semantic, semantic_status, covered = self._semantic_projection(
            note_paths, include=include_semantic
        )
        node_ids = {node.id for node in nodes}
        edges.extend(
            edge for edge in semantic if edge.source in node_ids and edge.target in node_ids
        )
        result = _page_response(
            scope="global",
            root=None,
            nodes=nodes,
            edges=edges,
            limit=bound_limit,
            offset=bound_offset,
            max_edges=self._settings.max_edges,
        )
        return result.model_copy(
            update={"semantic_status": semantic_status, "semantic_covered_nodes": covered}
        )

    # ------------------------------------------------------------------
    # Local scope (BFS from one note; depth/direction bounded)
    # ------------------------------------------------------------------

    def local_graph(
        self,
        note: str,
        *,
        depth: int = 1,
        direction: str = "both",
        limit: int | None = None,
        offset: int = 0,
        tag: str | None = None,
        include_broken: bool = True,
        include_semantic: bool = True,
    ) -> GraphResponse:
        self._index.assert_ready()
        path = _validated_input_text(note, "note path")
        bound_depth = _validated_depth(depth, self._settings)
        bound_direction = _validated_direction(direction)
        bound_limit = _validated_limit(limit, self._settings)
        bound_offset = _validated_offset(offset)
        folded = _validated_tag_filter(tag)
        if not self._index.note_exists(path):
            raise PathNotFound(path=path)
        snapshot = self._index.graph_snapshot()
        if path not in {item.path for item in snapshot.notes}:
            # Note vanished between the cheap gate and the coherent read.
            raise PathNotFound(path=path)
        maps = _build_maps(snapshot)
        all_notes = {item.path for item in snapshot.notes}
        forward, backward = _note_adjacency(snapshot, all_notes)

        visited: set[str] = {path}
        frontier: set[str] = {path}
        for _ in range(bound_depth):
            if not frontier:
                break
            neighbours: set[str] = set()
            for node in frontier:
                if bound_direction in ("both", "outgoing"):
                    neighbours.update(forward.get(node, ()))
                if bound_direction in ("both", "incoming"):
                    neighbours.update(backward.get(node, ()))
            neighbours.difference_update(visited)
            visited.update(neighbours)
            frontier = neighbours

        if folded is not None:
            matched = {item for item in visited if _note_has_tag(maps, item, folded)}
            if path in visited and path not in matched:
                # Root kept even when it does not carry the filter tag
                # (PLAN-M5 §10.2: keep the root and let the UI hint at the
                # active filter).
                matched.add(path)
            visited = matched

        nodes, edges = _scope_nodes_and_edges(
            maps,
            snapshot,
            note_paths=visited,
            folded_filter=folded,
            include_broken=include_broken,
        )
        candidates = {path} | set(sorted(all_notes - {path})[:499])
        semantic, semantic_status, covered = self._semantic_projection(
            candidates, include=include_semantic
        )
        if len(all_notes) > 500 and include_semantic:
            semantic_status = "limited"
        root_id = note_id(path)
        semantic_paths = {
            edge.target if edge.source == root_id else edge.source
            for edge in semantic
            if edge.source == root_id or edge.target == root_id
        }
        # Decode via the known edge endpoints, avoiding assumptions about path escaping.
        for item in snapshot.notes:
            if note_id(item.path) in semantic_paths:
                visited.add(item.path)
        if folded is not None:
            visited = {
                item for item in visited if item == path or _note_has_tag(maps, item, folded)
            }
        nodes, edges = _scope_nodes_and_edges(
            maps, snapshot, note_paths=visited, folded_filter=folded, include_broken=include_broken
        )
        visible_ids = {node.id for node in nodes}
        edges.extend(
            edge for edge in semantic if edge.source in visible_ids and edge.target in visible_ids
        )
        result = _page_response(
            scope="local",
            root=path,
            nodes=nodes,
            edges=edges,
            limit=bound_limit,
            offset=bound_offset,
            max_edges=self._settings.max_edges,
        )
        return result.model_copy(
            update={"semantic_status": semantic_status, "semantic_covered_nodes": covered}
        )

    # ------------------------------------------------------------------
    # Tag scope (PLAN-M5 §5.3: tag route == tag-filtered scope; 404 when
    # the tag was never written)
    # ------------------------------------------------------------------

    def tag_graph(
        self,
        tag: str,
        *,
        limit: int | None = None,
        offset: int = 0,
        include_broken: bool = True,
        include_semantic: bool = True,
    ) -> GraphResponse:
        self._index.assert_ready()
        folded = _validated_tag_filter(tag)
        assert folded is not None
        bound_limit = _validated_limit(limit, self._settings)
        bound_offset = _validated_offset(offset)
        snapshot = self._index.graph_snapshot()
        maps = _build_maps(snapshot)
        if folded not in maps.tag_displays:
            raise PathNotFound(path=tag)
        note_paths = {path for path in maps.tags_by_note if _note_has_tag(maps, path, folded)}
        nodes, edges = _scope_nodes_and_edges(
            maps,
            snapshot,
            note_paths=note_paths,
            folded_filter=folded,
            include_broken=include_broken,
        )
        semantic, semantic_status, covered = self._semantic_projection(
            note_paths, include=include_semantic
        )
        node_ids = {node.id for node in nodes}
        edges.extend(
            edge for edge in semantic if edge.source in node_ids and edge.target in node_ids
        )
        result = _page_response(
            scope="tag",
            root=None,
            nodes=nodes,
            edges=edges,
            limit=bound_limit,
            offset=bound_offset,
            max_edges=self._settings.max_edges,
        )
        return result.model_copy(
            update={"semantic_status": semantic_status, "semantic_covered_nodes": covered}
        )


__all__ = ["GraphService"]
