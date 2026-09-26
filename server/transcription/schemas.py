"""Wire DTOs for local audio transcription."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ..vault.schemas import RelativePath


class TranscriptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: RelativePath
    language: str | None = Field(
        default=None,
        max_length=32,
        pattern=r"[A-Za-z][A-Za-z0-9_-]{0,31}",
    )


class TranscriptionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    text: str
    language: str | None
    tool: str
    truncated: bool = False


__all__ = ["TranscriptionRequest", "TranscriptionResponse"]
