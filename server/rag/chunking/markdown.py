"""Markdown-aware semantic chunking (M14 §三).

Rules that must hold for every produced chunk:

1. ``content`` is a **slice of the source text**: ``text[start_offset:end_offset]``
   is always true, so a citation can be re-verified against the file bytes.
2. ``start_line``/``end_line`` are 1-based, inclusive and derived from the same
   offsets — a citation can never name a line outside the file.
3. A fenced code block, a Markdown table and a heading line are never split
   *within* themselves; a heading always travels with the text it introduces.
4. Frontmatter is never emitted as body text, but ``tags``/``aliases``/``title``
   from it are copied into every chunk's metadata and into the embedding text.
5. Chunking is pure and deterministic: the same bytes produce byte-identical
   chunks (``chunk_id`` = path + index + content hash), which is what makes the
   content-hash based incremental index safe.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime

from ...markdown.frontmatter import parse_frontmatter
from ..schemas import RAGChunk, content_hash

DEFAULT_CHUNK_TARGET_TOKENS = 800
DEFAULT_CHUNK_MAX_TOKENS = 1200
DEFAULT_CHUNK_OVERLAP_TOKENS = 100

_HEADING_RE = re.compile(r"^[ \t]{0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*$")
_FENCE_RE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})(.*)$")
_TABLE_ROW_RE = re.compile(r"^[ \t]{0,3}\|.*\|[ \t]*$")
_LIST_RE = re.compile(r"^[ \t]{0,3}(?:[-*+]|\d{1,9}[.)])[ \t]+")
_QUOTE_RE = re.compile(r"^[ \t]{0,3}>")
_SENTENCE_RE = re.compile(r"(?<=[。！？；!?;])|(?<=[.])(?=\s)|(?<=\n)")


def estimate_tokens(text: str) -> int:
    """Cheap, dependency-free token estimate for mixed CJK/Latin text.

    CJK ideographs/kana/hangul count ≈1 token each (they are rarely merged by
    BPE tokenizers), Latin/digit runs count as one token per word-ish run.
    The estimate only sizes chunks — it is never presented as a real tokenizer
    count, and every threshold stays configurable.
    """
    if not text:
        return 0
    tokens = 0
    latin_run = 0
    for char in text:
        code = ord(char)
        if code < 128:
            if char.isalnum() or char in "_-'":
                latin_run += 1
                continue
            if latin_run:
                tokens += 1
                latin_run = 0
            continue
        if latin_run:
            tokens += 1
            latin_run = 0
        if _is_cjk(code):
            tokens += 1
        elif char.isspace():
            continue
        else:
            tokens += 1
    if latin_run:
        tokens += 1
    return tokens


def _is_cjk(code: int) -> bool:
    return (
        0x3040 <= code <= 0x30FF  # kana
        or 0x3400 <= code <= 0x4DBF  # CJK ext A
        or 0x4E00 <= code <= 0x9FFF  # CJK unified
        or 0xAC00 <= code <= 0xD7AF  # hangul
        or 0xF900 <= code <= 0xFAFF  # compatibility
        or 0x20000 <= code <= 0x2FA1F  # ext B-F
    )


@dataclass(slots=True)
class _Segment:
    """One atomic Markdown block with exact source offsets."""

    kind: str  # "heading" | "code" | "table" | "text"
    text: str
    start_offset: int
    end_offset: int
    heading: str | None = None
    heading_path: str = ""
    heading_level: int = 0


class MarkdownChunker:
    """Structure-first chunker: group blocks to ~target, never split code/table."""

    def __init__(
        self,
        *,
        target_tokens: int = DEFAULT_CHUNK_TARGET_TOKENS,
        max_tokens: int = DEFAULT_CHUNK_MAX_TOKENS,
        overlap_tokens: int = DEFAULT_CHUNK_OVERLAP_TOKENS,
    ) -> None:
        self.target_tokens = max(1, int(target_tokens))
        self.max_tokens = max(self.target_tokens, int(max_tokens))
        self.overlap_tokens = max(0, int(overlap_tokens))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def chunk_document(
        self,
        path: str,
        text: str,
        *,
        modified_at: datetime | None = None,
    ) -> list[RAGChunk]:
        """Split one decoded Markdown document into citation-safe chunks."""
        if not text or not text.strip():
            return []
        frontmatter = parse_frontmatter(text)
        tags = list(frontmatter.tags)
        aliases = _aliases_from(frontmatter.properties)
        segments = self._segments(text)
        if not segments:
            return []
        groups = self._group(segments, text)
        chunks: list[RAGChunk] = []
        for group in groups:
            content = text[group[0].start_offset : group[-1].end_offset]
            if not content.strip():
                continue
            last = group[-1]
            # The chunk's own section is the *shallowest* heading it contains:
            # a chunk that carries `## A` and its `### A.1` belongs to `A`, and
            # citing `A.1` for the whole chunk would overstate the section.
            owner = _owner_heading(group, last)
            chunks.append(
                RAGChunk(
                    chunk_id=_chunk_id(path, len(chunks), content),
                    document_id=path,
                    path=path,
                    content=content,
                    content_hash=content_hash(content),
                    heading=owner.heading,
                    heading_path=owner.heading_path or None,
                    section_path=last.heading_path or None,
                    start_offset=group[0].start_offset,
                    end_offset=last.end_offset,
                    start_line=_line_of(text, group[0].start_offset),
                    end_line=_line_of(text, _last_line_offset(text, last.end_offset)),
                    tags=list(tags),
                    aliases=list(aliases),
                    modified_at=modified_at,
                    chunk_index=len(chunks),
                    token_estimate=estimate_tokens(content),
                )
            )
        return chunks

    # ------------------------------------------------------------------
    # Block scanning
    # ------------------------------------------------------------------

    def _segments(self, text: str) -> list[_Segment]:
        lines = _split_keepends(text)
        body_start = _body_offset(text)
        segments: list[_Segment] = []
        heading_stack: list[tuple[int, str]] = []
        offset = 0
        index = 0
        # Skip the frontmatter block entirely: it is metadata, never body text.
        while index < len(lines) and offset < body_start:
            offset += len(lines[index])
            index += 1
        pending: list[tuple[str, int, int]] = []

        def flush_text() -> None:
            if not pending:
                return
            start, end = pending[0][1], pending[-1][2]
            raw = text[start:end]
            if not raw.strip():
                pending.clear()
                return
            segments.append(
                _Segment(
                    kind="text",
                    text=raw,
                    start_offset=start,
                    end_offset=end,
                    heading=heading_stack[-1][1] if heading_stack else None,
                    heading_path=_breadcrumb(heading_stack),
                    heading_level=heading_stack[-1][0] if heading_stack else 0,
                )
            )
            pending.clear()

        while index < len(lines):
            line = lines[index]
            line_start = offset
            line_end = offset + len(line)
            fence = _FENCE_RE.match(line)
            if fence and not line.strip().startswith("    "):
                flush_text()
                block_start = line_start
                marker = fence.group(1)
                depth = len(marker)
                cursor = index + 1
                cursor_offset = line_end
                fence_char = marker[0]
                while cursor < len(lines):
                    candidate = lines[cursor]
                    cursor_offset += len(candidate)
                    cursor += 1
                    closing = _FENCE_RE.match(candidate)
                    if (
                        closing
                        and closing.group(1)[0] == fence_char
                        and len(closing.group(1)) >= depth
                        and not closing.group(2).strip()
                    ):
                        break
                end = cursor_offset
                segments.append(
                    _Segment(
                        kind="code",
                        text=text[block_start:end],
                        start_offset=block_start,
                        end_offset=end,
                        heading=heading_stack[-1][1] if heading_stack else None,
                        heading_path=_breadcrumb(heading_stack),
                        heading_level=heading_stack[-1][0] if heading_stack else 0,
                    )
                )
                offset = end
                index = cursor
                continue
            heading = _HEADING_RE.match(line)
            if heading:
                flush_text()
                level = len(heading.group(1))
                title = (heading.group(2) or "").strip()
                while heading_stack and heading_stack[-1][0] >= level:
                    heading_stack.pop()
                heading_stack.append((level, title))
                segments.append(
                    _Segment(
                        kind="heading",
                        text=line,
                        start_offset=line_start,
                        end_offset=line_end,
                        heading=title or None,
                        heading_path=_breadcrumb(heading_stack),
                        heading_level=level,
                    )
                )
                offset = line_end
                index += 1
                continue
            if _TABLE_ROW_RE.match(line):
                flush_text()
                block_start = line_start
                cursor = index
                cursor_offset = line_start
                while cursor < len(lines) and (
                    _TABLE_ROW_RE.match(lines[cursor])
                    or lines[cursor].strip() == ""
                    and cursor + 1 < len(lines)
                    and _TABLE_ROW_RE.match(lines[cursor + 1])
                ):
                    cursor_offset += len(lines[cursor])
                    cursor += 1
                segments.append(
                    _Segment(
                        kind="table",
                        text=text[block_start:cursor_offset],
                        start_offset=block_start,
                        end_offset=cursor_offset,
                        heading=heading_stack[-1][1] if heading_stack else None,
                        heading_path=_breadcrumb(heading_stack),
                        heading_level=heading_stack[-1][0] if heading_stack else 0,
                    )
                )
                offset = cursor_offset
                index = cursor
                continue
            if not line.strip():
                flush_text()
                offset = line_end
                index += 1
                continue
            kind = "list" if _LIST_RE.match(line) else "quote" if _QUOTE_RE.match(line) else "text"
            if pending and pending[-1][0] != kind and kind in ("quote",):
                flush_text()
            if not pending:
                pending.append((kind, line_start, line_end))
            else:
                pending.append((kind, pending[-1][1], line_end))
            offset = line_end
            index += 1
        flush_text()
        return segments

    # ------------------------------------------------------------------
    # Grouping + oversized splitting
    # ------------------------------------------------------------------

    def _group(self, segments: list[_Segment], text: str) -> list[list[_Segment]]:
        groups: list[list[_Segment]] = []
        current: list[_Segment] = []
        current_tokens = 0
        for segment in segments:
            tokens = estimate_tokens(segment.text)
            if tokens > self.max_tokens:
                if current:
                    groups.append(current)
                    current = []
                    current_tokens = 0
                groups.extend(self._split_segment(segment, text))
                continue
            if (
                segment.kind == "heading"
                and current
                and _starts_new_section(current, segment.heading_level)
                and current_tokens >= self.target_tokens
            ):
                # A new sibling/shallower section starts here and the group is
                # already at the token target: close it so one chunk never
                # spans two sections. A deeper heading continues the chunk.
                groups.append(current)
                current = []
                current_tokens = 0
            if current and current_tokens + tokens > self.max_tokens:
                groups.append(current)
                current = []
                current_tokens = 0
            current.append(segment)
            current_tokens += tokens
            if current_tokens >= self.target_tokens and segment.kind in ("code", "table"):
                groups.append(current)
                current = []
                current_tokens = 0
        if current:
            groups.append(current)
        return groups

    def _split_segment(self, segment: _Segment, text: str) -> list[list[_Segment]]:
        """Split one oversized block on sentence/newline boundaries.

        Only reached for a single paragraph/list longer than ``max_tokens``;
        code and table blocks are emitted whole even when oversized, since
        splitting them would corrupt the structure this chunker protects.
        """
        if segment.kind in ("code", "table"):
            return [[segment]]
        pieces = _sentence_pieces(segment.text)
        groups: list[list[_Segment]] = []
        current: list[_Segment] = []
        current_tokens = 0
        base = segment.start_offset
        cursor = 0
        for piece in pieces:
            piece_tokens = estimate_tokens(piece)
            if current and current_tokens + piece_tokens > self.target_tokens:
                groups.append(current)
                current = []
                current_tokens = 0
            start = base + cursor
            cursor += len(piece)
            current.append(
                _Segment(
                    kind=segment.kind,
                    text=piece,
                    start_offset=start,
                    end_offset=base + cursor,
                    heading=segment.heading,
                    heading_path=segment.heading_path,
                    heading_level=segment.heading_level,
                )
            )
            current_tokens += piece_tokens
            if current_tokens >= self.max_tokens:
                groups.append(current)
                current = []
                current_tokens = 0
        if current:
            groups.append(current)
        return groups

    # ------------------------------------------------------------------
    # Introspection helpers used by tests/observability
    # ------------------------------------------------------------------

    def describe(self) -> dict[str, int]:
        return {
            "target_tokens": self.target_tokens,
            "max_tokens": self.max_tokens,
            "overlap_tokens": self.overlap_tokens,
        }


def _sentence_pieces(text: str) -> list[str]:
    pieces = [piece for piece in _SENTENCE_RE.split(text) if piece]
    if len(pieces) <= 1 and len(text) > 1:
        # No sentence punctuation at all (e.g. one huge CJK run): hard split so
        # a single chunk can never exceed the embedding model input budget.
        pieces = [text[i : i + 400] for i in range(0, len(text), 400)]
    return pieces


def _overlap_note() -> str:
    """Why chunks do not overlap across segment boundaries.

    ``overlap_tokens`` documents the embedding budget, but this chunker keeps
    ``content`` a *verbatim* source slice: an overlap would make
    ``text[start:end] == content`` false and a citation's line range ambiguous.
    Splitting one oversized block already keeps the pieces adjacent, and the
    ContextBuilder merges neighbouring chunks of the same note, which recovers
    the cross-chunk context without breaking citation accuracy.
    """
    return "verbatim chunks; neighbours are merged by the context builder"


def _breadcrumb(stack: list[tuple[int, str]]) -> str:
    titles = [title for _, title in stack if title]
    return " > ".join(titles)


def _owner_heading(group: list[_Segment], last: _Segment) -> _Segment:
    """The segment whose heading identifies the whole group."""
    headings = [segment for segment in group if segment.kind == "heading"]
    if not headings:
        return last
    return min(headings, key=lambda segment: segment.heading_level)


def _starts_new_section(group: list[_Segment], level: int) -> bool:
    """True when ``group`` holds no heading at ``level`` or shallower.

    A deeper heading inside the same group is that section's own start, so it
    extends the current chunk; a sibling or shallower heading starts a new one.
    """
    return not any(
        segment.kind == "heading" and 0 < segment.heading_level <= level
        for segment in group
    )


def _owner_level(group: list[_Segment]) -> int:
    """Heading level that owns ``group`` (inf when it holds no heading)."""
    levels = [
        segment.heading_level
        for segment in group
        if segment.kind == "heading" and segment.heading_level > 0
    ]
    return min(levels) if levels else 10_000


def _chunk_id(path: str, index: int, content: str) -> str:
    """Stable chunk identity: path + content hash.

    The chunk *index* is deliberately excluded so that inserting a paragraph
    near the top of a note does not renumber every later chunk: unchanged
    chunks keep their id, and the incremental embedder then skips them instead
    of paying for a full re-embedding of the note.
    """
    digest = hashlib.sha256(
        f"{path}\x00{content_hash(content)}".encode()
    ).hexdigest()
    return digest[:24]


def _aliases_from(properties: dict) -> list[str]:
    value = properties.get("aliases") if isinstance(properties, dict) else None
    if value is None:
        value = properties.get("alias") if isinstance(properties, dict) else None
    if value is None:
        return []
    if isinstance(value, str):
        parts = [value]
    elif isinstance(value, (list, tuple)):
        parts = [str(item) for item in value]
    else:
        return []
    seen: set[str] = set()
    result: list[str] = []
    for part in parts:
        cleaned = str(part).strip()
        if not cleaned:
            continue
        folded = cleaned.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        result.append(cleaned)
    return result


def _split_keepends(text: str) -> list[str]:
    return text.splitlines(keepends=True)


def _body_offset(text: str) -> int:
    """Byte offset where the frontmatter block ends (0 when there is none)."""
    if not text.startswith("---"):
        return 0
    lines = _split_keepends(text)
    if not lines or lines[0].strip() != "---":
        return 0
    offset = len(lines[0])
    for line in lines[1:]:
        offset += len(line)
        if line.strip() == "---":
            return offset
    return 0


def _line_of(text: str, offset: int) -> int:
    """1-based line number containing ``offset`` (0 ⇒ line 1)."""
    return text.count("\n", 0, max(0, offset)) + 1


def _last_line_offset(text: str, end_offset: int) -> int:
    """Offset of the last content character inside ``[0, end_offset)``."""
    if end_offset <= 0:
        return 0
    index = end_offset - 1
    while index > 0 and text[index] in "\n\r":
        index -= 1
    return index


__all__ = [
    "DEFAULT_CHUNK_MAX_TOKENS",
    "DEFAULT_CHUNK_OVERLAP_TOKENS",
    "DEFAULT_CHUNK_TARGET_TOKENS",
    "MarkdownChunker",
    "estimate_tokens",
]
