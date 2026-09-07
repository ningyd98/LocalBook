"""REST endpoints for M3 outgoing links and backlinks (PLAN-M3 §5.7/§6.1).

Reads are served from the in-memory derived index (stale-by-event-window by
design; watcher events and ``POST /index/rebuild`` refresh it).  Note paths
use ``{note:path}`` plus the ``?path=`` query form; missing notes 404, an
unavailable index 503.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from ...links.schemas import BacklinksResponse, NoteLinksResponse
from ...links.service import LinksService
from ..dependencies import get_links_service

router = APIRouter(prefix="/api/v1", tags=["links"])
LinksServiceDep = Annotated[LinksService, Depends(get_links_service)]


@router.get("/links/{note:path}", response_model=NoteLinksResponse)
def get_note_links(note: str, service: LinksServiceDep) -> NoteLinksResponse:
    return service.outgoing(note)


@router.get("/links", response_model=NoteLinksResponse, include_in_schema=False)
def get_note_links_by_query(
    service: LinksServiceDep,
    path: str = Query(...),
) -> NoteLinksResponse:
    return service.outgoing(path)


@router.get("/backlinks/{note:path}", response_model=BacklinksResponse)
def get_note_backlinks(note: str, service: LinksServiceDep) -> BacklinksResponse:
    return service.backlinks(note)


@router.get("/backlinks", response_model=BacklinksResponse, include_in_schema=False)
def get_note_backlinks_by_query(
    service: LinksServiceDep,
    path: str = Query(...),
) -> BacklinksResponse:
    return service.backlinks(path)


__all__ = ["router"]
