"""M1/M3 Markdown boundary: raw bytes (no parser) + read-only M3 scanners.

``bytes`` remains the untouched M1 byte/hash boundary — never a parser.
``frontmatter`` and ``wikilinks`` are M3 *read-only* scanners of a minimal
syntax (PLAN-M3 §5.1/§5.2); they never read files, never rewrite or write
anything back.  File I/O stays exclusively in ``VaultService``.
"""

from .bytes import byte_length, sha256_bytes, snapshot
from .frontmatter import (
    FrontmatterResult,
    basename_no_extension,
    derive_title,
    parse_frontmatter,
    strip_frontmatter,
)
from .wikilinks import WikilinkRef, is_markdown_suffix, parse_wikilinks

__all__ = [
    "FrontmatterResult",
    "WikilinkRef",
    "basename_no_extension",
    "byte_length",
    "derive_title",
    "is_markdown_suffix",
    "parse_frontmatter",
    "parse_wikilinks",
    "sha256_bytes",
    "snapshot",
    "strip_frontmatter",
]
