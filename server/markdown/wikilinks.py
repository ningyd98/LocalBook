"""M3 wikilink / embed syntax scanner (PLAN-M3 §5.2).

A deliberately minimal, deterministic scanner for the five Obsidian-style
forms plus their combinations:

    [[Note]]                -> wikilink, target="Note"
    ![[Embed]]              -> embed,    target="Embed"
    [[Note#Heading]]        -> wikilink, target="Note", section="Heading"
    [[Note^Block]]          -> wikilink, target="Note", block="Block"
    [[Note|Alias]]          -> wikilink, target="Note", display="Alias"
    [[Note#Heading|Alias]]  -> wikilink, target="Note", section, display
    [[https://x]] / [[http://x]] -> web (never note-resolved)
    [[#Heading]]            -> wikilink, target="", section="Heading" (self)

Code inside fenced blocks (````` ``` `````) and inline code (`` ` ``) is
skipped so ``[[...]]`` inside code never becomes a link.  This module never
touches the Vault: resolution of ``target`` into a real path is the job of the
derived index / ``LinksService``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

_LINK_RE = re.compile(r"!?\[\[")
_FENCE_RE = re.compile(r"^[ \t]*(`{3,}|~{3,})")
_URL_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_CONTEXT_WINDOW = 200


@dataclass(frozen=True, slots=True)
class WikilinkRef:
    """One parsed wikilink/embed occurrence (pre-resolution)."""

    target: str
    raw: str
    kind: Literal["wikilink", "embed", "web"]
    display: str | None = None
    section: str | None = None
    block: str | None = None
    # Context line the link appeared on (trimmed, capped); used by backlinks.
    context: str | None = None
    # Resolution is filled in later by the index/LinksService.
    resolved_path: str | None = None
    broken: bool = False
    ambiguous: bool = False
    candidates: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "target": self.target,
            "raw": self.raw,
            "kind": self.kind,
            "display": self.display,
            "section": self.section,
            "block": self.block,
            "context": self.context,
            "resolved_path": self.resolved_path,
            "broken": self.broken,
            "ambiguous": self.ambiguous,
            "candidates": list(self.candidates),
        }


def _code_ranges(text: str) -> list[tuple[int, int]]:
    """Return [start, end) ranges of fenced blocks and inline code spans."""
    ranges: list[tuple[int, int]] = []
    lines = text.splitlines(keepends=True)
    fence_char: str | None = None
    fence_len = 0
    fence_start = 0
    offset = 0
    for line in lines:
        stripped = line.lstrip()
        fence_match = _FENCE_RE.match(stripped)
        if fence_match is not None:
            marker = fence_match.group(1)
            marker_char = marker[0]
            if fence_char is None:
                fence_char = marker_char
                fence_len = len(marker)
                fence_start = offset
            elif marker_char == fence_char and len(marker) >= fence_len:
                # Closing fence: the whole block (including both fences) is
                # excluded from link scanning.
                ranges.append((fence_start, offset + len(line)))
                fence_char = None
                fence_len = 0
        elif fence_char is not None:
            pass  # content inside an open fence is skipped
        if fence_char is None:
            # Inline code spans: a run of n backticks closes only at the next
            # run of exactly n backticks on the same line.  A run without a
            # matching closer is treated as stray punctuation (no skipping).
            cursor = 0
            while True:
                tick = line.find("`", cursor)
                if tick < 0:
                    break
                run_end = tick
                while run_end < len(line) and line[run_end] == "`":
                    run_end += 1
                run_len = run_end - tick
                closer = line.find("`" * run_len, run_end)
                if closer < 0:
                    break
                ranges.append((offset + tick, offset + closer + run_len))
                cursor = closer + run_len
        offset += len(line)
    if fence_char is not None:
        # Unterminated fence: skip everything from its opener to EOF.
        ranges.append((fence_start, offset))
    return ranges


def _is_inside(position: int, ranges: list[tuple[int, int]]) -> bool:
    for start, end in ranges:
        if start <= position < end:
            return True
    return False


def _line_context(text: str, position: int) -> str | None:
    """Trimmed text of the line containing ``position`` (capped)."""
    line_start = text.rfind("\n", 0, position) + 1
    line_end = text.find("\n", position)
    if line_end < 0:
        line_end = len(text)
    line = text[line_start:line_end].strip()
    if len(line) > _CONTEXT_WINDOW:
        line = line[:_CONTEXT_WINDOW].rstrip() + "…"
    return line or None


def _parse_inner(inner: str) -> tuple[str, str | None, str | None, str | None]:
    """Split ``target[#section][^block][|alias]``.

    Returns ``(target, section, block, alias)``.  The alias split happens on
    the first ``|``; the section/block split happens on the first ``#``/``^``
    inside the remaining part (targets never contain those separators).
    """
    alias: str | None = None
    if "|" in inner:
        inner, _, alias = inner.partition("|")
    section: str | None = None
    block: str | None = None
    if "#" in inner:
        inner, _, section = inner.partition("#")
    elif "^" in inner:
        inner, _, block = inner.partition("^")
    return inner.strip(), section.strip() if section else None, (
        block.strip() if block else None
    ), (alias.strip() if alias is not None and alias.strip() else None)


def parse_wikilinks(text: str) -> list[WikilinkRef]:
    """Scan ``text`` and return every link/embed outside code regions."""
    if not text:
        return []
    ranges = _code_ranges(text)
    results: list[WikilinkRef] = []
    position = 0
    while True:
        match = _LINK_RE.search(text, position)
        if match is None:
            break
        start = match.start()
        position = match.end()
        if _is_inside(start, ranges):
            continue
        close = text.find("]]", position)
        if close < 0:
            break
        raw = text[start : close + 2]
        inner = text[position:close].strip()
        if not inner:
            position = close + 2
            continue
        embed = raw.startswith("!")
        target, section, block, display = _parse_inner(inner)
        if _URL_SCHEME_RE.match(target):
            results.append(
                WikilinkRef(
                    target=target,
                    raw=raw,
                    kind="web",
                    display=display,
                    context=_line_context(text, start),
                )
            )
        else:
            results.append(
                WikilinkRef(
                    target=target,
                    raw=raw,
                    kind="embed" if embed else "wikilink",
                    display=display,
                    section=section,
                    block=block,
                    context=_line_context(text, start),
                )
            )
        position = close + 2
    return results


def is_markdown_suffix(path: str) -> bool:
    lowered = path.casefold()
    return lowered.endswith(".md") or lowered.endswith(".markdown")


__all__ = ["WikilinkRef", "is_markdown_suffix", "parse_wikilinks"]
