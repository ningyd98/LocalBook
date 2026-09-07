"""Pure Qwen3.5-4B matching rules (PLAN 4.5 / 8.1)."""

from __future__ import annotations

import pytest

from server.ai.matching import (
    DEFAULT_QWEN_MATCH_PATTERN,
    matches_pattern,
    matches_qwen35_4b,
    normalize_model_id,
)

# (model id, expected match)
MATCH_CASES = [
    # canonical spellings
    ("Qwen3.5-4B", True),
    ("qwen3.5-4b", True),
    ("qwen-3.5-4b", True),
    ("qwen_3.5_4b", True),
    ("qwen 3.5 4b", True),
    ("Qwen3.5-4B-Instruct", True),
    ("Qwen3.5-4B-Instruct-4bit", True),
    ("Qwen3.5-4B-gguf-q4_k_m", True),
    ("qwen35-4b", True),  # folded compatibility spelling
    ("QWEN3.5-4B-INSTRUCT", True),
    ("org/qwen3.5-4b-instruct", True),
    # look-alikes that must NOT match
    ("qwen", False),
    ("qwen2.5", False),
    ("Qwen2.5-4B", False),
    ("qwen3.5", False),
    ("Qwen3.5-8B", False),
    ("qwen3.5-14b", False),
    ("qwen3.5-32b", False),
    ("qwen2.5-72b-instruct", False),
    ("llama3.1-8b-instruct", False),
    ("text-embedding-nomic-embed-text", False),
    ("gpt-4o", False),
]


@pytest.mark.parametrize(("model_id", "expected"), MATCH_CASES)
def test_matches_qwen35_4b(model_id: str, expected: bool) -> None:
    assert matches_qwen35_4b(model_id) is expected, model_id


def test_normalize_model_id_folds_and_strips_separators() -> None:
    assert normalize_model_id("Qwen3.5-4B-Instruct-4bit") == "qwen354binstruct4bit"
    assert normalize_model_id("qwen-3.5-4b") == "qwen354b"
    assert normalize_model_id("  Qwen  3.5 _ 4b  ") == "qwen354b"
    # Unicode-safe: full-width characters only casefold with str.casefold.
    assert normalize_model_id("QWEN3.5-4B") == "qwen354b"


def test_default_pattern_equals_documented_contract() -> None:
    # The pure-function default matches the config default in PLAN 4.4.
    assert DEFAULT_QWEN_MATCH_PATTERN == r"qwen3\.?5[-_ ]?4b"
    assert matches_pattern("Qwen3.5-4B-Instruct", DEFAULT_QWEN_MATCH_PATTERN) is True
    assert matches_pattern("qwen3.5-8b", DEFAULT_QWEN_MATCH_PATTERN) is False


def test_custom_pattern_override_is_honored() -> None:
    # A user-supplied pattern is applied to the folded id, e.g. match a
    # 32b Qwen instead of the 4b default.
    pattern = r"qwen3\.?5[-_ ]?32b"
    assert matches_qwen35_4b("Qwen3.5-32B-Instruct", pattern) is True
    assert matches_qwen35_4b("Qwen3.5-4B-Instruct", pattern) is False
