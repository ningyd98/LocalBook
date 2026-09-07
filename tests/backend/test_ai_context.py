"""ContextBuilder limits/heading/dedupe/safety tests (PLAN-M6 §9.1.5)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from server.ai.context import ContextBuilder, ContextSource


def _source(
    path: str, text: str, *, heading: str | None = None, relevance: float | None = None
) -> ContextSource:
    return ContextSource(
        path=path, title=path.rsplit("/", 1)[-1], text=text, heading=heading, relevance=relevance
    )


def test_per_note_truncation() -> None:
    builder = ContextBuilder(max_notes=4, max_chars_per_note=10, max_chars_total=1000)
    bundle = builder.build([_source("a.md", "x" * 40)])
    assert bundle.notes[0].text == "x" * 10
    assert bundle.notes[0].truncated is True
    assert bundle.total_chars == 10


def test_total_char_cap_is_shared_across_notes() -> None:
    builder = ContextBuilder(max_notes=10, max_chars_per_note=100, max_chars_total=25)
    bundle = builder.build([_source("a.md", "x" * 20), _source("b.md", "y" * 20)])
    # First note fits fully (20), second is sliced to the remaining 5 chars.
    assert [n.path for n in bundle.notes] == ["a.md", "b.md"]
    assert bundle.notes[1].text == "y" * 5
    assert bundle.total_chars == 25


def test_note_count_cap() -> None:
    builder = ContextBuilder(max_notes=2, max_chars_per_note=100, max_chars_total=1000)
    notes = [_source(f"n{i}.md", f"text-{i}") for i in range(5)]
    bundle = builder.build(notes)
    assert len(bundle.notes) == 2
    assert bundle.omitted_notes == 3


def test_duplicate_paths_dedupe_keeping_first() -> None:
    builder = ContextBuilder(max_notes=5, max_chars_per_note=100, max_chars_total=1000)
    sources = [_source("a.md", "first"), _source("b.md", "other"), _source("a.md", "dup")]
    bundle = builder.build(sources)
    assert [n.path for n in bundle.notes] == ["a.md", "b.md"]
    assert bundle.notes[0].text == "first"


def test_current_note_is_prioritized_first() -> None:
    builder = ContextBuilder(max_notes=5, max_chars_per_note=100, max_chars_total=1000)
    sources = [
        _source("z.md", "zzz", relevance=9.0),
        _source("a.md", "aaa", relevance=1.0),
    ]
    # First listed note is the "primary" note and always stays first.
    bundle = builder.build(sources)
    assert [n.path for n in bundle.notes] == ["z.md", "a.md"]


def test_relevance_orders_secondary_notes() -> None:
    builder = ContextBuilder(max_notes=5, max_chars_per_note=100, max_chars_total=1000)
    sources = [
        _source("primary.md", "p", relevance=0.0),
        _source("low.md", "l", relevance=1.0),
        _source("high.md", "h", relevance=9.0),
    ]
    bundle = builder.build(sources)
    assert [n.path for n in bundle.notes] == ["primary.md", "high.md", "low.md"]


def test_unsafe_paths_are_dropped() -> None:
    builder = ContextBuilder(max_notes=10, max_chars_per_note=100, max_chars_total=1000)
    sources = [
        _source("/etc/passwd", "abs"),
        _source("../escape.md", "up"),
        _source(r"back\slash.md", "backslash"),
        _source(".localnote/state.json", "hidden"),
        _source("nested/ok.md", "fine"),
    ]
    bundle = builder.build(sources)
    assert [n.path for n in bundle.notes] == ["nested/ok.md"]


def test_heading_slice_extracts_section_only() -> None:
    text = "# Top\nintro\n## Section\ninside body\nmore\n## Next\nother"
    builder = ContextBuilder(max_notes=5, max_chars_per_note=100, max_chars_total=1000)
    bundle = builder.build([_source("a.md", text, heading="Section")])
    sliced = bundle.notes[0].text
    assert "## Section" in sliced
    assert "inside body" in sliced
    assert "intro" not in sliced
    assert "## Next" not in sliced
    # Audit I1: the slice must join lines with a real newline, never the
    # two-character literal backslash-n escape, so downstream prompt text is
    # line separated instead of containing visible "\n" sequences.
    assert "\n" in sliced
    assert "\\n" not in sliced
    assert sliced.splitlines() == ["## Section", "inside body", "more"]


def test_empty_sources_produce_empty_bundle() -> None:
    bundle = ContextBuilder().build([])
    assert bundle.notes == []
    assert bundle.total_chars == 0
    assert bundle.omitted_notes == 0


def test_source_extra_fields_are_forbidden() -> None:
    with pytest.raises(ValidationError):
        ContextSource(path="a.md", title="a", text="t", sneaky=True)  # type: ignore[call-arg]
