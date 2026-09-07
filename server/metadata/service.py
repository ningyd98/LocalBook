"""M3 Metadata domain service (PLAN-M3 §5.1/§5.6).

Reads the current note bytes through ``VaultService`` (the only FS façade)
and parses frontmatter read-only.  Parse failures are never transport errors:
they are rendered as ``frontmatter_status: parse_error/unreadable`` with a
structured ``parse_error`` at HTTP 200.  The route still depends on the
derived index so index unavailability yields the plan's 503 boundary.
"""

from __future__ import annotations

from typing import Any

from ..markdown.frontmatter import (
    basename_no_extension,
    derive_title,
    parse_frontmatter,
    strip_frontmatter,
)
from ..vault.service import VaultService
from .schemas import MetadataParseError, NoteMetadataResponse

_DECODE_MESSAGE = "File is not valid UTF-8"
_PARSE_KIND_BY_CODE = {
    "yaml": "yaml",
    "unterminated": "unterminated",
    "non_dict": "non_dict",
    "yaml_unavailable": "yaml_unavailable",
}


def _parse_error_from_result(error: dict[str, Any] | None) -> MetadataParseError | None:
    if error is None:
        return None
    return MetadataParseError(
        kind=_PARSE_KIND_BY_CODE.get(str(error.get("kind")), "other"),
        message=str(error.get("message", "Frontmatter could not be parsed")),
        line=error.get("line"),
    )


class MetadataService:
    def __init__(self, vault: VaultService) -> None:
        self._vault = vault

    def get(self, path: str) -> NoteMetadataResponse:
        data, _digest = self._vault.read_bytes(path)
        stem = basename_no_extension(path)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return NoteMetadataResponse(
                path=path,
                title=stem,
                frontmatter_status="unreadable",
                properties={},
                tags=[],
                parse_error=MetadataParseError(
                    kind="decode", message=_DECODE_MESSAGE, line=None
                ),
                available=True,
            )
        result = parse_frontmatter(text)
        body = strip_frontmatter(text)
        title = derive_title(body, stem)
        return NoteMetadataResponse(
            path=path,
            title=title,
            frontmatter_status=result.status,  # type: ignore[arg-type]
            properties=result.properties,
            tags=result.tags,
            parse_error=_parse_error_from_result(result.parse_error),
            available=True,
        )


__all__ = ["MetadataService"]
