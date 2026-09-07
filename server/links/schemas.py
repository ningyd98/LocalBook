"""M3 outgoing-link / backlink DTOs (PLAN-M3 §5.2/§6.1)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class LinkRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str
    raw: str
    kind: Literal["wikilink", "embed", "web"]
    display: str | None = None
    section: str | None = None
    block: str | None = None
    resolved_path: str | None = None
    broken: bool = False
    ambiguous: bool = False
    candidates: list[str] = []


class NoteLinksResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    outgoing: list[LinkRef]
    broken_count: int
    generated_at: datetime


class BacklinkRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_path: str
    title: str
    text: str | None = None


class BacklinksResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    backlinks: list[BacklinkRef]
    count: int
    generated_at: datetime


__all__ = ["BacklinkRef", "BacklinksResponse", "LinkRef", "NoteLinksResponse"]
