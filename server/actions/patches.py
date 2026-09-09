"""Small format-preserving Markdown patch helpers for M7 (PLAN-M7 §5.3).

None of these helpers serialize YAML or re-emit a whole note; they only
insert/remove lines inside the frontmatter ``tags`` list or append a wikilink
at a safe body position.  Unknown frontmatter keys, BOMs, CRLF/LF endings and
all body bytes are preserved.  When an input shape cannot be proven safe, the
helpers raise :class:`UnsupportedPatch` and the action is denied instead of
touching the file.
"""

from __future__ import annotations

import re

_TAG_HEADER_RE = re.compile(r"tags:[ \t]*(?:#[^\r\n]*)?$")
_TAG_ITEM_RE = re.compile(r"^[ \t]{0,4}[-*][ \t]+([^\r\n]*?)[ \t]*$")
_FENCE_RE = re.compile(r"(?m)^[ \t]*```")


class UnsupportedPatch(ValueError):
    """The note shape does not admit a provably safe patch; deny the action."""


def _strip_bom(text: str) -> tuple[str, int]:
    """Return (text without a leading U+FEFF, number of skipped chars)."""
    if text.startswith("\ufeff"):
        return text[1:], 1
    return text, 0


def _frontmatter_span(text: str) -> tuple[int, int] | None:
    """Return the slice covering frontmatter incl. the final content newline.

    Tolerates a leading BOM and CRLF line endings; the returned end points at
    the newline that precedes the closing ``---`` delimiter.
    """
    cleaned, _bom = _strip_bom(text)
    if not cleaned.startswith("---"):
        return None
    first_end = cleaned.find("\n")
    if first_end < 0 or cleaned[:first_end].strip() != "---":
        return None
    marker = cleaned.find("\n---", first_end + 1)
    if marker < 0:
        return None
    return first_end + 1, marker + 1


def _tag_block_lines(
    frontmatter: str, base: int
) -> tuple[list[tuple[str, int, int]], int] | None:
    """Return ``tags:`` header + item lines with absolute spans, or None."""
    header_start: int | None = None
    cursor = 0
    result: list[tuple[str, int, int]] = []
    in_items = False
    block_end = -1
    for raw in frontmatter.splitlines(keepends=True):
        abs_start = base + cursor
        abs_end = abs_start + len(raw)
        cursor += len(raw)
        line = raw.rstrip("\r\n")
        if not in_items:
            if header_start is None and _TAG_HEADER_RE.match(line):
                header_start = abs_start
                in_items = True
                result.append((raw, abs_start, abs_end))
            continue
        if _TAG_ITEM_RE.match(line):
            result.append((raw, abs_start, abs_end))
            continue
        if not line.strip():
            continue
        block_end = abs_start
        break
    if header_start is None:
        return None
    if block_end < 0:
        block_end = base + cursor
    return result, block_end


def _tag_block_abs(frontmatter: str, base: int) -> tuple[int, int] | None:
    found = _tag_block_lines(frontmatter, base)
    if found is None:
        return None
    lines, block_end = found
    return lines[0][1], block_end


def has_tags_block(source: str) -> bool:
    span = _frontmatter_span(source)
    if span is None:
        return False
    start, end = span
    return _tag_block_abs(source[start:end], start) is not None


def add_tags(source: str, tags: list[str]) -> str:
    """Add ``tags`` to an existing tags block, else insert a block."""
    text, bom = _strip_bom(source)
    newline = "\r\n" if "\r\n" in text else "\n"
    addition = "".join(f"  - {tag}{newline}" for tag in tags)
    span = _frontmatter_span(text)
    prefix = "\ufeff" if bom else ""
    if span is None:
        return prefix + f"---{newline}tags:{newline}" + addition + f"---{newline}" + text
    start, end = span
    block = _tag_block_abs(text[start:end], start)
    if block is not None:
        _block_start, block_end = block
        return prefix + text[:block_end] + addition + text[block_end:]
    # An existing scalar/flow-style or quoted key is not an absent field.
    # Refuse formats we cannot patch faithfully instead of creating a second
    # key that would silently hide the original tags from YAML readers.
    if re.search(r"(?m)^(?:tags|'tags'|\"tags\")[ \t]*:", text[start:end]):
        raise UnsupportedPatch("existing tags format cannot be safely extended")
    return prefix + text[:end] + f"tags:{newline}" + addition + text[end:]


def remove_tags(source: str, tags: list[str]) -> str:
    """Remove explicit tags from the frontmatter tags list (BOM/CRLF safe)."""
    text, bom = _strip_bom(source)
    prefix = "\ufeff" if bom else ""
    span = _frontmatter_span(text)
    if span is None:
        raise UnsupportedPatch("note has no frontmatter; nothing to remove")
    start, end = span
    found = _tag_block_lines(text[start:end], start)
    if found is None:
        raise UnsupportedPatch("note has no tags block; nothing to remove")
    lines, _block_end = found
    wanted = {tag.casefold() for tag in tags}
    kept: list[tuple[str, int, int]] = []
    removed = 0
    for raw, abs_start, abs_end in lines:
        item = _TAG_ITEM_RE.match(raw.rstrip("\r\n"))
        if item is not None and item.group(1).strip().casefold() in wanted:
            removed += 1
            continue
        kept.append((raw, abs_start, abs_end))
    if removed == 0:
        raise UnsupportedPatch("requested tags were not present in the note")
    surviving = [
        (raw, s, e)
        for raw, s, e in kept
        if _TAG_ITEM_RE.match(raw.rstrip("\r\n")) is not None
    ]
    if not surviving:
        block_start = lines[0][1]
        block_end = lines[-1][2]
        return prefix + text[:block_start] + text[block_end:]
    block_start = lines[0][1]
    block_end = lines[-1][2]
    rebuilt = "".join(raw for raw, _s, _e in kept)
    return prefix + text[:block_start] + rebuilt + text[block_end:]


def _inside_fence_at_end(source: str) -> bool:
    fences = len(_FENCE_RE.findall(source))
    return fences % 2 == 1


def add_link(source: str, target: str) -> str:
    """Append one wikilink at a safe end-of-body position."""
    if _inside_fence_at_end(source):
        raise UnsupportedPatch("cannot append a link inside an open code fence")
    body = source.rstrip("\n").rstrip("\r")
    link = f"[[{target}]]"
    return body + "\n\n" + link + "\n"


def apply_hunks(
    data: bytes,
    hunks: list[object],
    *,
    max_output_bytes: int = 5_000_000,
) -> bytes:
    """Apply bounded hunks in ascending start order with exact context match."""
    text = data.decode("utf-8")
    offset = 0
    for raw in sorted(hunks, key=lambda hunk: int(getattr(hunk, "start", 0))):
        start = int(getattr(raw, "start", 0)) + offset
        old_text = str(getattr(raw, "old_text", "") or "")
        new_text = str(getattr(raw, "new_text", "") or "")
        if start < 0 or start + len(old_text) > len(text):
            raise UnsupportedPatch("hunk start out of range")
        if text[start : start + len(old_text)] != old_text:
            raise UnsupportedPatch("patch context mismatch")
        text = text[:start] + new_text + text[start + len(old_text) :]
        offset += len(new_text) - len(old_text)
    result = text.encode("utf-8")
    if len(result) > max_output_bytes:
        raise UnsupportedPatch("patch output exceeds the size limit")
    return result


def tags_only_change(before: str, after: str) -> bool:
    """True when both texts differ only inside the frontmatter tags block."""
    return _strip_tags_block(before) == _strip_tags_block(after)


def _strip_tags_block(text: str) -> str:
    cleaned, bom = _strip_bom(text)
    span = _frontmatter_span(cleaned)
    if span is None:
        return text
    start, end = span
    block = _tag_block_abs(cleaned[start:end], start)
    if block is None:
        return text
    block_start, block_end = block
    prefix = "\ufeff" if bom else ""
    return prefix + cleaned[:block_start] + cleaned[block_end:]


__all__ = [
    "UnsupportedPatch",
    "add_link",
    "add_tags",
    "apply_hunks",
    "has_tags_block",
    "remove_tags",
    "tags_only_change",
]
