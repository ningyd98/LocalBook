"""REST endpoints for the M5 read-only graph (PLAN-M5 §5.3/§5.4).

``GET /api/v1/graph`` — global note/tag scope, optionally ``tag=``-filtered;
``GET /api/v1/graph/local/{note:path}`` — BFS scope around one note with
``depth``/``direction`` bounds;
``GET /api/v1/graph/tag/{tag:path}`` (plus the ``?tag=`` query alias) — the
tag-filtered tag scope; an unknown tag is 404 ``not_found``.

Semantic parameter bounds (limit ≤ 2000, offset ≥ 0, depth ≤ 3, direction
enum, control/empty/over-long path or tag values) raise the M1
``invalid_request`` domain error (HTTP 400); an unavailable index maps to
503 ``index_unavailable`` through the existing error handler.  The graph is
always read-only: nothing here writes to the Vault, the Markdown body or
SQLite.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from ...graph.schemas import GraphResponse
from ...graph.service import GraphService
from ..dependencies import get_graph_service

router = APIRouter(prefix="/api/v1", tags=["graph"])
GraphServiceDep = Annotated[GraphService, Depends(get_graph_service)]


@router.get("/graph", response_model=GraphResponse)
def get_global_graph(
    service: GraphServiceDep,
    limit: int | None = Query(default=None, ge=1),
    offset: int = Query(default=0),
    tag: str | None = Query(default=None),
    include_broken: bool = Query(default=True),
) -> GraphResponse:
    return service.global_graph(
        limit=limit,
        offset=offset,
        tag=tag,
        include_broken=include_broken,
    )


@router.get("/graph/local/{note:path}", response_model=GraphResponse)
def get_local_graph(
    note: str,
    service: GraphServiceDep,
    depth: int = Query(default=1),
    direction: str = Query(default="both"),
    limit: int | None = Query(default=None, ge=1),
    offset: int = Query(default=0),
    tag: str | None = Query(default=None),
    include_broken: bool = Query(default=True),
) -> GraphResponse:
    return service.local_graph(
        note,
        depth=depth,
        direction=direction,
        limit=limit,
        offset=offset,
        tag=tag,
        include_broken=include_broken,
    )


@router.get("/graph/tag/{tag:path}", response_model=GraphResponse)
def get_tag_graph(
    tag: str,
    service: GraphServiceDep,
    limit: int | None = Query(default=None, ge=1),
    offset: int = Query(default=0),
    include_broken: bool = Query(default=True),
) -> GraphResponse:
    return service.tag_graph(
        tag,
        limit=limit,
        offset=offset,
        include_broken=include_broken,
    )


@router.get("/graph/tag", response_model=GraphResponse, include_in_schema=False)
def get_tag_graph_by_query(
    service: GraphServiceDep,
    tag: str = Query(...),
    limit: int | None = Query(default=None, ge=1),
    offset: int = Query(default=0),
    include_broken: bool = Query(default=True),
) -> GraphResponse:
    return service.tag_graph(
        tag,
        limit=limit,
        offset=offset,
        include_broken=include_broken,
    )


__all__ = ["router"]
