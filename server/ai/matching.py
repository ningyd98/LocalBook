"""Pure Qwen3.5-4B model-id matching rules (Phase 0 — no network, no IO).

Rule (docs/ai-architecture.md):
  1. casefold the model id;
  2. strip separator characters (whitespace, ``_``, ``.``, ``-``);
  3. apply the configured regex pattern to the folded id.

The default pattern matches ids that contain ``qwen`` + ``3.5`` (the folded
form also accepts the ``qwen35`` spelling) + ``4b``, e.g.:
``Qwen3.5-4B-Instruct-4bit``, ``qwen-3.5-4b``, ``qwen35-4b``.

It rejects look-alikes such as ``qwen``, ``qwen2.5-4b`` and ``qwen3.5-8b``.

A specific complete model id is never hardcoded; the pattern is configurable
via ``LOCALNOTE_AI__QWEN_MATCH_PATTERN``.
"""

from __future__ import annotations

import re

_SEPARATOR_RE = re.compile(r"[\s._-]+")

DEFAULT_QWEN_MATCH_PATTERN = r"qwen3\.?5[-_ ]?4b"


def normalize_model_id(model_id: str) -> str:
    """Fold a model id for comparison: casefold + strip separators.

    >>> normalize_model_id("Qwen3.5-4B-Instruct-4bit")
    'qwen354binstruct4bit'
    >>> normalize_model_id("qwen-3.5-4b")
    'qwen354b'
    """
    return _SEPARATOR_RE.sub("", model_id.casefold())


def matches_pattern(model_id: str, pattern: str) -> bool:
    """True when ``pattern`` (a regex) matches the folded model id."""
    return re.search(pattern, normalize_model_id(model_id)) is not None


def matches_qwen35_4b(model_id: str, pattern: str | None = None) -> bool:
    """Default Qwen3.5-4B matcher.

    ``pattern`` defaults to :data:`DEFAULT_QWEN_MATCH_PATTERN`; it is applied
    to the folded (casefolded, separator-stripped) id.
    """
    return matches_pattern(model_id, pattern or DEFAULT_QWEN_MATCH_PATTERN)
