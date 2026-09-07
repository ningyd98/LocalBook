"""M3 keyword-search DTOs (PLAN-M3 §5.5/§6.1)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class SearchHit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    title: str
    snippet: str
    matched_terms: list[str]
    score: float


class SearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    hits: list[SearchHit]
    total: int
    degraded: bool
    skipped_notes: int
    generated_at: datetime


__all__ = ["SearchHit", "SearchResponse"]
