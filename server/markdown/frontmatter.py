"""M3 read-only YAML frontmatter parsing (PLAN-M3 §5.1).

This module is intentionally a *parser of a minimal syntax*, not a Markdown
parser and never a serializer: it never reads files, never rewrites text and
never writes anything back.  Callers that need file bytes must go through
``VaultService.read_bytes``.

Fixed rules (PLAN-M3 §5.1):
- Input is already-decoded ``str``.  A leading UTF-8 BOM is stripped before
  delimiter detection; CRLF line endings are normalised for *reading* only.
- Only a first-line ``---`` opener is recognised (Obsidian convention); a
  leading blank line means ``status: "none"``.
- The YAML block ends at the next line whose content is ``---`` (trailing
  whitespace tolerated).  A missing closer is ``parse_error: unterminated``.
- ``yaml.safe_load`` only (never ``yaml.load``/custom tags).  A successful
  non-``dict`` document is ``parse_error: non_dict`` (an empty ``---\\n---``
  block yields ``None`` and therefore also ``non_dict``, per the plan's
  dict-or-error rule).
- ``tags`` is normalised: each element / comma+whitespace separated token is
  trimmed, stripped of a leading ``#``, empties dropped, order preserved and
  duplicates removed; original case kept for display, ``tags_folded``
  (casefold) kept for indexing.
- A missing ``yaml`` import degrades to ``parse_error: yaml_unavailable``;
  Vault I/O and editing are unaffected.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

try:  # PyYAML is a locked runtime dep; degrade gracefully if import fails.
    import yaml
    from yaml.error import YAMLError
except ImportError:  # pragma: no cover - exercised via import-path tests
    yaml = None  # type: ignore[assignment]
    YAMLError = Exception  # type: ignore[misc,assignment]

_LINE_SPLIT_RE = re.compile(r"\r\n|\r|\n")
_DELIMITER_LINE = re.compile(r"^---\s*$")
_SECTION_RE = re.compile(r"^[ \t]*---[ \t]*$")

_SAFE_PARSE_ERROR_MESSAGES: dict[str, str] = {
    "unterminated": "Frontmatter has no closing --- delimiter",
    "yaml": "Frontmatter is not valid YAML",
    "non_dict": "Frontmatter must be a YAML mapping",
    "yaml_unavailable": "YAML support is unavailable on this server",
}


@dataclass(frozen=True, slots=True)
class FrontmatterResult:
    """Parse outcome for one note (PLAN-M3 §5.1 return contract)."""

    status: str  # "none" | "ok" | "parse_error"
    properties: dict[str, Any]
    tags: list[str]
    tags_folded: list[str]
    parse_error: dict[str, Any] | None


def _split_tags(value: Any) -> list[str]:
    """Normalise one tags value into ordered, de-duplicated tag strings."""
    raw_parts: list[Any]
    if isinstance(value, str):
        raw_parts = [value]
    elif isinstance(value, (list, tuple)):
        raw_parts = list(value)
    elif value is None:
        return []
    else:
        raw_parts = [value]
    seen: set[str] = set()
    result: list[str] = []
    for part in raw_parts:
        for token in re.split(r"[\s,]+", str(part).strip()):
            if not token:
                continue
            cleaned = token[1:] if token.startswith("#") else token
            cleaned = cleaned.strip()
            if not cleaned:
                continue
            folded = cleaned.casefold()
            if folded in seen:
                continue
            seen.add(folded)
            result.append(cleaned)
    return result


def _coerce_json_safe(value: Any) -> Any:
    """Make ``yaml.safe_load`` output JSON-serialisable without losing shape.

    ``safe_load`` normally yields only str/int/float/bool/None/list/dict, but
    its implicit timestamp handling produces ``datetime.date``/``datetime``
    values and keys need not be strings.  Those rare YAML forms are coerced
    (date→ISO-8601, key→str); every other value passes through untouched so
    unknown fields keep their original scalar type.
    """
    if isinstance(value, dict):
        return {
            (str(key) if not isinstance(key, str) else key): _coerce_json_safe(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_coerce_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_coerce_json_safe(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def parse_frontmatter(text: str) -> FrontmatterResult:
    """Parse the optional first-line YAML frontmatter of a decoded note.

    Never raises on malformed input: every failure becomes a structured
    ``parse_error`` entry inside the result.
    """
    source = text
    if source.startswith("\ufeff"):
        source = source[1:]
    lines = _LINE_SPLIT_RE.split(source)
    if not lines or not _SECTION_RE.match(lines[0]):
        return FrontmatterResult(
            status="none",
            properties={},
            tags=[],
            tags_folded=[],
            parse_error=None,
        )

    closing_index: int | None = None
    for index in range(1, len(lines)):
        if _DELIMITER_LINE.match(lines[index]):
            closing_index = index
            break
    if closing_index is None:
        return FrontmatterResult(
            status="parse_error",
            properties={},
            tags=[],
            tags_folded=[],
            parse_error={
                "kind": "unterminated",
                "message": _SAFE_PARSE_ERROR_MESSAGES["unterminated"],
                "line": 1,
            },
        )

    if yaml is None:  # pragma: no cover - depends on environment import
        return FrontmatterResult(
            status="parse_error",
            properties={},
            tags=[],
            tags_folded=[],
            parse_error={
                "kind": "yaml_unavailable",
                "message": _SAFE_PARSE_ERROR_MESSAGES["yaml_unavailable"],
                "line": None,
            },
        )

    yaml_text = "\n".join(lines[1:closing_index])
    try:
        loaded = yaml.safe_load(yaml_text)
    except YAMLError as exc:
        line = getattr(getattr(exc, "problem_mark", None), "line", None)
        return FrontmatterResult(
            status="parse_error",
            properties={},
            tags=[],
            tags_folded=[],
            parse_error={
                "kind": "yaml",
                "message": _SAFE_PARSE_ERROR_MESSAGES["yaml"],
                # problem_mark.line is 0-based inside the YAML block; add the
                # opener line offset (line 1) plus one for 1-based reporting.
                "line": (line + 2) if isinstance(line, int) else None,
            },
        )
    except Exception:
        # Defensive: any unexpected loader failure is a per-note diagnostic,
        # never a crash for the whole Vault.
        return FrontmatterResult(
            status="parse_error",
            properties={},
            tags=[],
            tags_folded=[],
            parse_error={
                "kind": "yaml",
                "message": _SAFE_PARSE_ERROR_MESSAGES["yaml"],
                "line": None,
            },
        )

    if not isinstance(loaded, dict):
        return FrontmatterResult(
            status="parse_error",
            properties={},
            tags=[],
            tags_folded=[],
            parse_error={
                "kind": "non_dict",
                "message": _SAFE_PARSE_ERROR_MESSAGES["non_dict"],
                # The closing delimiter occupies 1-based line closing_index+1.
                "line": closing_index + 1,
            },
        )

    properties = _coerce_json_safe(loaded)
    tags = _split_tags(properties.get("tags"))
    return FrontmatterResult(
        status="ok",
        properties=properties,
        tags=tags,
        tags_folded=[tag.casefold() for tag in tags],
        parse_error=None,
    )


def strip_frontmatter(text: str) -> str:
    """Return the note body with any first-line frontmatter block removed.

    Mirrors ``parse_frontmatter`` delimiter rules; when there is no opener or
    no closer the whole text is returned unchanged (the body is never
    rewritten by this module — this only selects a substring for search).
    """
    source = text
    if source.startswith("\ufeff"):
        source = source[1:]
    lines = _LINE_SPLIT_RE.split(source)
    if not lines or not _SECTION_RE.match(lines[0]):
        return text
    for index in range(1, len(lines)):
        if _DELIMITER_LINE.match(lines[index]):
            remainder = lines[index + 1 :]
            return "\n".join(remainder)
    return text


def derive_title(text: str, fallback: str) -> str:
    """First level-1 heading without its ``#`` markers, else ``fallback``."""
    for line in text.splitlines():
        if re.match(r"^[ \t]{0,3}#(?!#)[ \t]+(.+)$", line):
            title = re.sub(r"^[ \t]{0,3}#(?!#)[ \t]+", "", line).strip()
            if title:
                return title
    return fallback


def basename_no_extension(path: str) -> str:
    """``notes/a/b.md`` → ``b``; a path without an extension stays as-is."""
    name = path.rsplit("/", 1)[-1]
    if "." in name:
        stem, _, extension = name.rpartition(".")
        if extension and stem:
            return stem
    return name


__all__ = [
    "FrontmatterResult",
    "basename_no_extension",
    "derive_title",
    "parse_frontmatter",
    "strip_frontmatter",
]
