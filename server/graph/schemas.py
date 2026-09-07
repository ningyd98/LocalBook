"""M5 Graph REST DTOs (PLAN-M5 §5.1, wire contract mirrored in TS).

The graph is a *read-only, query-time* projection of the M4 derived index
(``notes``/``tags``/``links``): nothing in this module writes, and the DTOs
are deliberately stable so ``packages/protocol`` can mirror them exactly.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

GraphNodeType = Literal["note", "tag"]
GraphEdgeType = Literal["link", "backlink", "tag"]
GraphScope = Literal["global", "local", "tag"]


class GraphNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str  # opaque stable ID (`note:`/`tag:` + percent-encoded key)
    type: GraphNodeType
    label: str
    path: str | None = None  # note only (root-relative POSIX path)
    title: str | None = None  # note only
    tag: str | None = None  # tag only (deterministic display)
    tag_folded: str | None = None  # tag only (casefold key)


class GraphEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    source: str
    target: str  # empty only for broken/dangling edges with no target node
    type: GraphEdgeType
    directed: bool = True
    raw: str | None = None
    resolved_path: str | None = None
    section: str | None = None
    block: str | None = None
    broken: bool = False
    ambiguous: bool = False
    candidates: list[str] = []
    context: str | None = None


class GraphPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int
    offset: int
    next_offset: int | None
    total_nodes: int
    total_edges: int
    truncated: bool


class GraphResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: Literal["note-tag-v1"]
    scope: GraphScope
    root: str | None  # local scope: the root note path; otherwise None
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    page: GraphPage
    generated_at: datetime


__all__ = [
    "GraphEdge",
    "GraphEdgeType",
    "GraphNode",
    "GraphNodeType",
    "GraphPage",
    "GraphResponse",
    "GraphScope",
]
