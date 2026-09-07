"""M3 frontmatter parser matrix (PLAN-M3 §5.1/§9.1).

Pure text-level tests: the parser never reads files, so these run on inline
strings.  End-to-end note metadata (VaultService → parse) is covered by
``test_metadata_service.py`` and ``test_m3_api.py``.
"""

from __future__ import annotations

import pytest

from server.markdown import frontmatter as fm


def _parse(body: str) -> fm.FrontmatterResult:
    return fm.parse_frontmatter(body)


def test_ok_preserves_unknown_fields_and_types() -> None:
    text = (
        "---\n"
        "title: 我的笔记\n"
        "emoji: 😀\n"
        "count: 3\n"
        "enabled: true\n"
        "nothing: null\n"
        "custom_unknown:\n"
        "  nested: [1, 2, x]\n"
        "---\n"
        "# Body\n"
    )
    result = _parse(text)
    assert result.status == "ok"
    assert result.parse_error is None
    props = result.properties
    assert props["title"] == "我的笔记"
    assert props["emoji"] == "😀"
    assert props["count"] == 3
    assert props["enabled"] is True
    assert props["nothing"] is None
    assert props["custom_unknown"] == {"nested": [1, 2, "x"]}


def test_tags_normalization_variants() -> None:
    cases = {
        'tags: "工作, 重要"': ["工作", "重要"],
        "tags: [工作, 重要, 工作]": ["工作", "重要"],
        'tags: "#work"': ["work"],
        'tags: "#a, b"': ["a", "b"],
        'tags: "a b"': ["a", "b"],
        "tags: [x, x, y]": ["x", "y"],
    }
    for yaml_line, expected in cases.items():
        result = _parse(f"---\n{yaml_line}\n---\n")
        assert result.status == "ok"
        assert result.tags == expected
        assert result.tags_folded == [tag.casefold() for tag in expected]


def test_tags_case_preserved_for_display_folded_for_index() -> None:
    result = _parse('---\ntags: "Work, work"\n---\n')
    assert result.tags == ["Work"]
    assert result.tags_folded == ["work"]


def test_tags_missing_is_empty() -> None:
    result = _parse("---\ntitle: x\n---\n")
    assert result.status == "ok"
    assert result.tags == []
    assert result.tags_folded == []


def test_no_frontmatter_when_first_line_is_not_delimiter() -> None:
    for text in ("# heading\nbody", "text\n---\ntags: x\n---\n", "   \n---\nx: 1\n---\n"):
        result = _parse(text)
        assert result.status == "none"
        assert result.properties == {}
        assert result.tags == []
        assert result.parse_error is None


def test_unterminated_frontmatter_is_parse_error() -> None:
    result = _parse("---\ntitle: x\nnever closed")
    assert result.status == "parse_error"
    assert result.parse_error is not None
    assert result.parse_error["kind"] == "unterminated"
    assert result.properties == {}
    assert "delimiter" in result.parse_error["message"]


def test_invalid_yaml_is_parse_error() -> None:
    result = _parse('---\ntitle: "unterminated quote\n---\n')
    assert result.status == "parse_error"
    assert result.parse_error is not None
    assert result.parse_error["kind"] == "yaml"
    assert isinstance(result.parse_error.get("line"), int) or result.parse_error.get("line") is None


def test_non_dict_yaml_document_is_parse_error() -> None:
    for yaml_doc in ("- a\n- b", "42", '"just a string"'):
        result = _parse(f"---\n{yaml_doc}\n---\n")
        assert result.status == "parse_error"
        assert result.parse_error is not None
        assert result.parse_error["kind"] == "non_dict"


def test_empty_frontmatter_block_is_non_dict_parse_error() -> None:
    # PLAN-M3 §5.1 rule 4 applies strictly: an empty YAML document loads as
    # None, which is not a dict, so it is a per-note diagnostic.
    result = _parse("---\n---\n# Body")
    assert result.status == "parse_error"
    assert result.parse_error is not None
    assert result.parse_error["kind"] == "non_dict"


def test_bom_and_crlf_are_recognized() -> None:
    text = "\ufeff---\r\ntitle: BOM CRLF\r\ntags: '#tag'\r\n---\r\n# Body\r\n"
    result = _parse(text)
    assert result.status == "ok"
    assert result.properties["title"] == "BOM CRLF"
    assert result.tags == ["tag"]


def test_yaml_unavailable_degrades_to_parse_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fm, "yaml", None)
    result = _parse("---\ntitle: x\n---\n")
    assert result.status == "parse_error"
    assert result.parse_error is not None
    assert result.parse_error["kind"] == "yaml_unavailable"


def test_date_value_coerced_to_iso_string_for_json() -> None:
    result = _parse("---\ndue: 2024-01-15\n---\n")
    assert result.status == "ok"
    assert result.properties["due"] == "2024-01-15"


def test_parser_never_raises_on_junk() -> None:
    for junk in ("", "\n", "---\n", "---\ntitle: x\n---\n", "\ufeff", "a" * 5000):
        _parse(junk)  # must not raise


def test_strip_frontmatter_returns_body() -> None:
    text = "---\ntags: [a]\n---\n# Body\ncontent"
    assert fm.strip_frontmatter(text) == "# Body\ncontent"
    # no opener → unchanged; unterminated → unchanged
    assert fm.strip_frontmatter("# Only") == "# Only"
    assert fm.strip_frontmatter("---\ntags: x\n") == "---\ntags: x\n"


def test_derive_title_and_basename_helpers() -> None:
    assert fm.derive_title("# Hello\n## not h1", "fallback") == "Hello"
    assert fm.derive_title("## Not h1\nbody", "fallback") == "fallback"
    assert fm.derive_title("  #  Heading  \n", "fb") == "Heading"
    assert fm.basename_no_extension("notes/Ref A.md") == "Ref A"
    assert fm.basename_no_extension("noext") == "noext"
