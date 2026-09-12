"""Context builder: retrieval results → EvidencePack (M14 §八).

This is the grounding boundary. Everything the model is allowed to see passes
through here, and the resulting :class:`~server.rag.schemas.EvidencePack` is the
only thing the citation validator trusts.

Responsibilities
----------------
- **deduplicate** hits (same chunk, and identical text reached by two retrievers);
- **merge** adjacent chunks of one document into one evidence block, so a
  passage split by chunking is not quoted twice;
- **budget** the context by tokens, both per document and in total, so one very
  long note cannot crowd out every other note;
- **keep provenance**: each source carries path, heading, section and the exact
  line range, which is what the answer's citations are rendered from.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..chunking.markdown import estimate_tokens
from ..schemas import EvidencePack, RetrievalResult, SourceEvidence

DEFAULT_MAX_CHUNKS = 6
DEFAULT_MAX_TOKENS = 4000
DEFAULT_MAX_TOKENS_PER_DOCUMENT = 1800
DEFAULT_MERGE_GAP_LINES = 5


@dataclass(slots=True)
class ContextBudget:
    """Configurable limits for one evidence pack."""

    max_chunks: int = DEFAULT_MAX_CHUNKS
    max_tokens: int = DEFAULT_MAX_TOKENS
    max_tokens_per_document: int = DEFAULT_MAX_TOKENS_PER_DOCUMENT
    merge_adjacent: bool = True
    merge_gap_lines: int = DEFAULT_MERGE_GAP_LINES

    @classmethod
    def from_settings(cls, settings) -> ContextBudget:
        return cls(
            max_chunks=int(getattr(settings, "context_top_k", DEFAULT_MAX_CHUNKS)),
            max_tokens=int(getattr(settings, "context_max_tokens", DEFAULT_MAX_TOKENS)),
            max_tokens_per_document=int(
                getattr(
                    settings,
                    "context_max_tokens_per_document",
                    DEFAULT_MAX_TOKENS_PER_DOCUMENT,
                )
            ),
            merge_adjacent=bool(getattr(settings, "context_merge_adjacent", True)),
            merge_gap_lines=int(
                getattr(settings, "context_merge_gap_lines", DEFAULT_MERGE_GAP_LINES)
            ),
        )


@dataclass(slots=True)
class EvidenceCandidate:
    """One accumulated evidence block before numbering."""

    path: str
    heading: str | None
    heading_path: str | None
    content: str
    start_line: int
    end_line: int
    chunk_ids: list[str]
    content_hashes: list[str]
    score: float = 0.0
    tags: list[str] | None = None

    @property
    def chunk_id(self) -> str:
        return self.chunk_ids[0] if self.chunk_ids else ""

    @property
    def content_hash(self) -> str:
        return self.content_hashes[0] if self.content_hashes else ""


class RagContextBuilder:
    """Turn ranked retrieval results into one grounded evidence pack."""

    def __init__(self, budget: ContextBudget | None = None) -> None:
        self.budget = budget or ContextBudget()

    # ------------------------------------------------------------------

    def build(
        self,
        query: str,
        results: Sequence[RetrievalResult],
        *,
        candidate_count: int | None = None,
        max_chunks: int | None = None,
        max_tokens: int | None = None,
    ) -> EvidencePack:
        limit_chunks = int(max_chunks) if max_chunks else self.budget.max_chunks
        limit_tokens = int(max_tokens) if max_tokens else self.budget.max_tokens
        candidates = self._accumulate(results)
        selected: list[EvidenceCandidate] = []
        per_document: dict[str, int] = {}
        used_tokens = 0
        truncated = False
        for candidate in candidates:
            if len(selected) >= max(0, limit_chunks):
                truncated = True
                break
            tokens = estimate_tokens(candidate.content)
            spent = per_document.get(candidate.path, 0)
            per_document_cap = self.budget.max_tokens_per_document
            if spent and spent + tokens > per_document_cap:
                truncated = True
                continue  # this document already used its share of the budget
            if used_tokens + tokens > limit_tokens and selected:
                truncated = True
                break
            if not spent and tokens > per_document_cap:
                # The first chunk of a document is always admitted (an answer
                # needs at least something to cite); the document is then
                # closed, so a very long note still cannot crowd out others.
                selected.append(candidate)
                per_document[candidate.path] = per_document_cap + 1
                used_tokens += tokens
                truncated = True
                continue
            selected.append(candidate)
            per_document[candidate.path] = spent + tokens
            used_tokens += tokens

        sources = [
            SourceEvidence(
                source_id=f"S{index}",
                path=candidate.path,
                heading=candidate.heading,
                heading_path=candidate.heading_path,
                start_line=candidate.start_line,
                end_line=candidate.end_line,
                content=candidate.content,
                content_hash=candidate.content_hash,
                chunk_id=candidate.chunk_id,
            )
            for index, candidate in enumerate(selected, start=1)
        ]
        return EvidencePack(
            query=query,
            sources=sources,
            context_tokens=used_tokens,
            candidate_count=(
                int(candidate_count)
                if candidate_count is not None
                else len(results)
            ),
            truncated=truncated,
        )

    # ------------------------------------------------------------------

    def _accumulate(
        self, results: Sequence[RetrievalResult]
    ) -> list[EvidenceCandidate]:
        """Deduplicate by chunk/text and merge adjacent chunks of a document."""
        ordered: list[EvidenceCandidate] = []
        position: dict[int, int] = {}
        seen_chunks: set[str] = set()
        seen_text: set[str] = set()
        by_document: dict[str, list[EvidenceCandidate]] = {}

        for item in results:
            if item.chunk_id and item.chunk_id in seen_chunks:
                continue
            text_key = (item.content_hash or item.content).strip()
            if text_key and text_key in seen_text:
                continue
            if item.chunk_id:
                seen_chunks.add(item.chunk_id)
            if text_key:
                seen_text.add(text_key)
            candidate = EvidenceCandidate(
                path=item.path,
                heading=item.heading,
                heading_path=item.heading_path,
                content=item.content,
                start_line=item.start_line,
                end_line=item.end_line,
                chunk_ids=[item.chunk_id] if item.chunk_id else [],
                content_hashes=[item.content_hash] if item.content_hash else [],
                score=item.score,
                tags=list(item.tags),
            )
            by_document.setdefault(item.path, []).append(candidate)
            position[id(candidate)] = len(ordered)
            ordered.append(candidate)

        if not self.budget.merge_adjacent:
            return ordered

        merged: list[EvidenceCandidate] = []
        for group in by_document.values():
            group.sort(key=lambda item: (item.start_line, item.end_line))
            current: EvidenceCandidate | None = None
            for candidate in group:
                if current is None:
                    current = candidate
                    continue
                if candidate.start_line - current.end_line <= self.budget.merge_gap_lines:
                    current = self._join(current, candidate)
                    continue
                merged.append(current)
                current = candidate
            if current is not None:
                merged.append(current)

        # Keep the fused (score) order of the *first* chunk of each merged
        # block: merging must not reshuffle the ranking the user sees.
        merged.sort(
            key=lambda item: position.get(
                id(item), _merged_position(item, position, ordered)
            )
        )
        return merged

    @staticmethod
    def _join(left: EvidenceCandidate, right: EvidenceCandidate) -> EvidenceCandidate:
        """Concatenate two adjacent blocks of one document into one citation."""
        separator = "" if left.content.endswith("\n") else "\n"
        return EvidenceCandidate(
            path=left.path,
            heading=left.heading or right.heading,
            heading_path=left.heading_path or right.heading_path,
            content=f"{left.content}{separator}{right.content}",
            start_line=min(left.start_line, right.start_line),
            end_line=max(left.end_line, right.end_line),
            chunk_ids=[*left.chunk_ids, *right.chunk_ids],
            content_hashes=[*left.content_hashes, *right.content_hashes],
            score=max(left.score, right.score),
            tags=left.tags or right.tags,
        )


def _merged_position(
    merged: EvidenceCandidate,
    position: dict[int, int],
    ordered: list[EvidenceCandidate],
) -> int:
    """Position of a merged block = the earliest position of its members."""
    candidates = [
        position.get(id(item))
        for item in ordered
        if item.chunk_ids and set(item.chunk_ids) & set(merged.chunk_ids)
    ]
    resolved = [value for value in candidates if value is not None]
    return min(resolved) if resolved else len(ordered)


def render_evidence_pack(pack: EvidencePack) -> str:
    """Render the pack as the model-facing context (stable ``[S1]`` labels)."""
    blocks: list[str] = []
    for source in pack.sources:
        header = [
            f"[{source.source_id}]",
            f"path: {source.path}",
        ]
        if source.heading_path or source.heading:
            header.append(f"heading: {source.heading_path or source.heading}")
        header.append(f"lines: {source.start_line}-{source.end_line}")
        blocks.append(
            "\n".join(header) + "\ncontent:\n" + source.content.rstrip("\n")
        )
    return "\n\n".join(blocks)


__all__ = [
    "ContextBudget",
    "DEFAULT_MAX_CHUNKS",
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_MAX_TOKENS_PER_DOCUMENT",
    "EvidenceCandidate",
    "RagContextBuilder",
    "render_evidence_pack",
]
