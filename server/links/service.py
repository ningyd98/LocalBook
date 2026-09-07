"""M3 Links domain service (PLAN-M3 §5.2/§5.6).

Resolution happens inside the derived index (basename maps); this service only
reads index rows and shapes DTOs.  A note missing from the index (unreadable
at build time or genuinely absent) surfaces as the standard ``not_found``
domain error; index unavailability raises ``IndexUnavailable`` (503).
"""

from __future__ import annotations

from datetime import UTC, datetime

from ..index.service import DerivedIndexService
from ..markdown.wikilinks import WikilinkRef
from ..vault.errors import PathNotFound
from .schemas import BacklinkRef, BacklinksResponse, LinkRef, NoteLinksResponse


def _to_link(ref: WikilinkRef) -> LinkRef:
    return LinkRef(
        target=ref.target,
        raw=ref.raw,
        kind=ref.kind,  # type: ignore[arg-type]
        display=ref.display,
        section=ref.section,
        block=ref.block,
        resolved_path=ref.resolved_path,
        broken=ref.broken,
        ambiguous=ref.ambiguous,
        candidates=list(ref.candidates),
    )


class LinksService:
    def __init__(self, index: DerivedIndexService) -> None:
        self._index = index

    def outgoing(self, path: str) -> NoteLinksResponse:
        self._index.assert_ready()
        entry = self._index.entry(path)
        if entry is None:
            raise PathNotFound(path=path)
        refs = [_to_link(ref) for ref in entry.outgoing]
        broken_count = sum(1 for ref in refs if ref.broken)
        return NoteLinksResponse(
            path=path,
            outgoing=refs,
            broken_count=broken_count,
            generated_at=datetime.now(UTC),
        )

    def backlinks(self, path: str) -> BacklinksResponse:
        self._index.assert_ready()
        entry = self._index.entry(path)
        if entry is None:
            raise PathNotFound(path=path)
        result: list[BacklinkRef] = []
        for source_path, ref in self._index.backlink_sources(path):
            source = self._index.entry(source_path)
            result.append(
                BacklinkRef(
                    source_path=source_path,
                    title=source.title if source is not None else source_path,
                    text=ref.context,
                )
            )
        return BacklinksResponse(
            path=path,
            backlinks=result,
            count=len(result),
            generated_at=datetime.now(UTC),
        )


__all__ = ["LinksService"]
