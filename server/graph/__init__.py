"""M5 Graph: read-only, query-time note/tag projection of the M4 index.

This package owns the graph DTOs (``schemas.py``) and the stateless
projection service (``service.py``).  It performs **zero** writes: every
request reads one coherent :class:`~server.index.schemas.GraphSnapshot`
through ``DerivedIndexService`` and never touches Vault files or the
Markdown body (PLAN-M5 §3.4/§5.4).
"""

from __future__ import annotations

from .schemas import (
    GraphEdge,
    GraphEdgeType,
    GraphNode,
    GraphNodeType,
    GraphPage,
    GraphResponse,
    GraphScope,
)
from .service import GraphService

__all__ = [
    "GraphEdge",
    "GraphEdgeType",
    "GraphNode",
    "GraphNodeType",
    "GraphPage",
    "GraphResponse",
    "GraphScope",
    "GraphService",
]
