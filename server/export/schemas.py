"""Response DTOs for the export endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ExportAttachment(BaseModel):
    """One reference found in the note that resolved to a Vault file."""

    model_config = ConfigDict(extra="forbid")

    #: The reference exactly as authored (``img.png``, ``./a/b.png``, …).
    ref: str
    #: Vault-relative URL written into ``markdown`` for this reference.
    url: str
    #: Vault-relative path of the target file.
    path: str
    mime: str
    size: int
    #: ``data:`` URI, present only when the attachment was actually inlined.
    data_uri: str | None = None
    inlined: bool = False
    #: Why it was *not* inlined (``attachment_too_large``, ``export_size_limit``).
    reason: str | None = None


class ExportWarning(BaseModel):
    """A reference that could not be embedded, reported instead of failing."""

    model_config = ConfigDict(extra="forbid")

    code: str
    ref: str
    message: str


class ExportNoteResponse(BaseModel):
    """Manifest consumed by the web app to print a self-contained PDF."""

    model_config = ConfigDict(extra="forbid")

    path: str
    title: str
    download_name: str
    #: Note body with every resolvable reference rewritten to ``url``.
    markdown: str
    attachments: list[ExportAttachment] = Field(default_factory=list)
    warnings: list[ExportWarning] = Field(default_factory=list)
    #: Total inlined attachment bytes.
    inlined_bytes: int = 0
    #: True when at least one attachment was skipped because of a size cap.
    truncated: bool = False
    service_version: str
    generated_at: datetime
    format: Literal["manifest"] = "manifest"


__all__ = [
    "ExportAttachment",
    "ExportNoteResponse",
    "ExportWarning",
]
