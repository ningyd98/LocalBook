"""M3 derived-index DTOs and the in-memory note entry (PLAN-M3 §5.3/§6.1).

``IndexRebuildResponse`` is the REST contract; ``NoteIndexEntry`` is the
per-note runtime row of the in-memory (non-SQLite) index.  Entries are plain
dataclasses because the service mutates/rebuilds them under one lock.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from ..markdown.wikilinks import WikilinkRef

FrontmatterStatus = Literal["none", "ok", "parse_error", "unreadable"]


class IndexRebuildResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    indexed: int
    skipped: int
    failed: int
    duration_ms: float
    ready: bool
    generated_at: datetime


@dataclass(slots=True)
class NoteIndexEntry:
    """One indexed markdown note (PLAN-M3 §5.3)."""

    path: str
    sha256: str
    title: str
    basename: str
    text: str
    text_truncated: bool
    tags: list[str] = field(default_factory=list)
    tags_folded: list[str] = field(default_factory=list)
    properties: dict[str, Any] = field(default_factory=dict)
    frontmatter_status: FrontmatterStatus = "none"
    parse_error: dict[str, Any] | None = None
    outgoing: list[WikilinkRef] = field(default_factory=list)
    resolved_targets: set[str] = field(default_factory=set)
    search_folded: str = ""
    diagnostic: str | None = None


@dataclass(frozen=True, slots=True)
class GraphNoteRow:
    """One note row of the M5 read-only graph projection (PLAN-M5 §5.3)."""

    path: str
    title: str
    basename: str


@dataclass(frozen=True, slots=True)
class GraphTagRow:
    """One ``tags`` row of the M5 read-only graph projection.

    ``seq`` keeps the frontmatter order so note→tag edges can be reproduced
    deterministically.
    """

    note_path: str
    tag: str
    tag_folded: str
    seq: int


@dataclass(frozen=True, slots=True)
class GraphLinkRow:
    """One ``links`` row of the M5 read-only graph projection.

    This is a copy of the row's fields (not the sqlite3.Row) so the snapshot
    is safe to consume after the index lock has been released.
    """

    source_path: str
    seq: int
    target: str
    raw: str
    kind: str
    display: str | None = None
    section: str | None = None
    block: str | None = None
    context: str | None = None
    resolved_path: str | None = None
    broken: bool = False
    ambiguous: bool = False
    candidates: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GraphSnapshot:
    """One coherent, lock-held read of every M4 row the graph needs.

    M5 computes the graph at query time from the M4 tables (PLAN-M5 §3.4):
    no graph tables, no extra migrations.  The snapshot is the full ordered
    notes/tags/links projection; :class:`server.graph.service.GraphService`
    derives scopes, edges, pagination and truncation from it purely in
    Python.  Lists are sorted deterministically so repeated requests with an
    unchanged index reproduce identical DTOs.
    """

    notes: tuple[GraphNoteRow, ...] = ()
    tags: tuple[GraphTagRow, ...] = ()
    links: tuple[GraphLinkRow, ...] = ()


@dataclass(frozen=True, slots=True)
class GraphTotals:
    """Aggregate counts over the M4 tables (diagnostics/perf evidence)."""

    note_count: int
    tag_count: int
    link_count: int


@dataclass(frozen=True, slots=True)
class SearchCandidate:
    """A lightweight per-note search row handed to ``SearchService``.

    M4 internal contract between the SQLite-backed index and the search
    service (PLAN-M4 §5.4/§5.5): the index owns the SQL and returns these
    rows; SearchService owns scoring, snippets and the ``SearchResponse``
    DTO.  ``score`` carries the raw FTS5 ``bm25`` value for FTS rows
    (``0.0`` for substring rows, whose score is computed later).
    """

    path: str
    title: str
    basename: str
    text: str
    tags_folded: str = ""
    score: float = 0.0


__all__ = [
    "FrontmatterStatus",
    "GraphLinkRow",
    "GraphNoteRow",
    "GraphSnapshot",
    "GraphTagRow",
    "GraphTotals",
    "IndexRebuildResponse",
    "NoteIndexEntry",
    "SearchCandidate",
]
