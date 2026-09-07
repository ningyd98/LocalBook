"""History/Journal DTOs for M7 (PLAN-M7 §5.5).

History is derived audit data stored in the M4 ``.localnote/index.db`` via the
additive ``ai_jobs`` / ``ai_job_journal`` tables.  Rules:

- every model is ``extra="forbid"``;
- note bodies only ever appear in ``diff``/``journal`` fields, both capped;
- no prompt text, tokens, absolute roots or credentials are stored (see
  :func:`redact_secrets`).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

_MAX_ACTIONS_STORED = 20
_MAX_FILES_READ_STORED = 200
_MAX_DIFF_ENTRIES = 20
_MAX_UNIFIED_DIFF_CHARS = 200_000
_MAX_JOURNAL_BYTES_BASE64 = 14_000_000  # ~10 MB decoded before base64
_MAX_MESSAGE_CHARS = 2_000

_SECRET_PATTERN = re.compile(
    r"(?i)((?:authorization\s*[:=]\s*)?bearer\s+[a-z0-9._=+/:-]{4,}|"
    r"authorization\s*[:=]\s*[^\s,;]{4,}|"
    r"(?:token|api[_-]?key|secret|password)\s*[:=]\s*[^\s,;]{4,})"
)


def redact_secrets(value: str) -> str:
    """Replace credential-shaped fragments with a fixed placeholder."""
    return _SECRET_PATTERN.sub("***", value)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _safe_path(value: object) -> object:
    if isinstance(value, str):
        return value[:4096]
    return value


class HistoryRecord(BaseModel):
    """One durable AI job record (mirrors the ``ai_jobs`` row)."""

    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(min_length=1, max_length=128)
    task_type: str = Field(default="manual", max_length=64)
    model: str | None = Field(default=None, max_length=200)
    prompt_version: str | None = Field(default=None, max_length=100)
    permission_level: int = Field(default=1, ge=0, le=2)
    start_time: str = Field(default_factory=_now_iso)
    end_time: str | None = None
    status: str = Field(default="planned", max_length=32)
    error: dict[str, Any] | None = None
    files_read: list[str] = Field(default_factory=list, max_length=_MAX_FILES_READ_STORED)
    proposed_actions: list[dict[str, Any]] = Field(
        default_factory=list, max_length=_MAX_ACTIONS_STORED
    )
    executed_actions: list[dict[str, Any]] = Field(
        default_factory=list, max_length=_MAX_ACTIONS_STORED
    )
    diff: list[dict[str, Any]] = Field(default_factory=list, max_length=_MAX_DIFF_ENTRIES)
    before_hash: dict[str, str | None] = Field(default_factory=dict)
    after_hash: dict[str, str | None] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_now_iso)

    @field_validator("files_read")
    @classmethod
    def _valid_files(cls, values: list[str]) -> list[str]:
        return [str(_safe_path(value)) for value in values]

    @field_validator("error")
    @classmethod
    def _valid_error(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if isinstance(item, str):
                item = redact_secrets(item)[:_MAX_MESSAGE_CHARS]
            cleaned[str(key)[:64]] = item
        return cleaned

    @field_validator("diff")
    @classmethod
    def _valid_diff(cls, values: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for entry in values:
            unified = entry.get("unified_diff")
            if isinstance(unified, str) and len(unified) > _MAX_UNIFIED_DIFF_CHARS:
                entry = {**entry, "unified_diff": unified[:_MAX_UNIFIED_DIFF_CHARS]}
        return values


class JournalEntry(BaseModel):
    """One durable transaction step (mirrors the ``ai_job_journal`` row)."""

    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(min_length=1, max_length=128)
    seq: int = Field(ge=0)
    operation: str = Field(max_length=16)  # create | update | move
    path: str = Field(max_length=4096)
    before_exists: bool = True
    before_bytes_base64: str | None = None
    before_hash: str | None = None
    after_exists: bool = True
    after_hash: str | None = None
    inverse: dict[str, Any] = Field(default_factory=dict)
    state: str = Field(default="pending", max_length=32)

    @field_validator("before_bytes_base64")
    @classmethod
    def _bound_before_bytes(cls, value: str | None) -> str | None:
        if value is not None and len(value) > _MAX_JOURNAL_BYTES_BASE64:
            raise ValueError("journal before-state exceeds the size limit")
        return value


class HistoryPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[HistoryRecord] = Field(default_factory=list)
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)


__all__ = [
    "HistoryPage",
    "HistoryRecord",
    "JournalEntry",
    "redact_secrets",
]
