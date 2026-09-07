"""GET /api/v1/health — fixed liveness response.

Deliberately independent of Vault, AI, SQLite or any future service: it only
proves the API process is alive. Exact body: ``{"status":"ok"}``.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/v1", tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
