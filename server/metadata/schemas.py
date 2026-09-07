"""M3 frontmatter/Metadata read DTOs (PLAN-M3 §5.1/§6.1)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

MetadataParseErrorKind = Literal[
    "yaml", "unterminated", "non_dict", "decode", "yaml_unavailable", "other"
]


class MetadataParseError(BaseModel):
    kind: MetadataParseErrorKind
    message: str
    line: int | None = None


class NoteMetadataResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    title: str
    frontmatter_status: Literal["none", "ok", "parse_error", "unreadable"]
    properties: dict[str, Any]
    tags: list[str]
    parse_error: MetadataParseError | None = None
    available: bool = True


__all__ = ["MetadataParseError", "MetadataParseErrorKind", "NoteMetadataResponse"]
