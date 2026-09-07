"""M3 wikilink scanner matrix (PLAN-M3 §5.2/§9.1).

Text-level only — resolution into real note paths happens in the index /
``LinksService`` and is covered by ``test_index_service.py``/``test_links.py``.
"""

from __future__ import annotations

from server.markdown.wikilinks import parse_wikilinks


def _single(text: str):
    refs = parse_wikilinks(text)
    assert len(refs) == 1, [r.raw for r in refs]
    return refs[0]


def test_plain_wikilink() -> None:
    ref = _single("see [[Ref A]] there")
    assert ref.target == "Ref A"
    assert ref.raw == "[[Ref A]]"
    assert ref.kind == "wikilink"
    assert ref.display is None and ref.section is None and ref.block is None
    assert ref.context == "see [[Ref A]] there"


def test_embed_prefix() -> None:
    ref = _single("![[img.png]]")
    assert ref.kind == "embed"
    assert ref.target == "img.png"
    assert ref.raw == "![[img.png]]"


def test_heading_section() -> None:
    ref = _single("[[Note#Heading]]")
    assert ref.kind == "wikilink"
    assert ref.target == "Note"
    assert ref.section == "Heading"
    assert ref.display is None


def test_block_reference() -> None:
    ref = _single("[[Note^Block]]")
    assert ref.kind == "wikilink"
    assert ref.target == "Note"
    assert ref.block == "Block"


def test_alias_display() -> None:
    ref = _single("[[Note|Alias Text]]")
    assert ref.target == "Note"
    assert ref.display == "Alias Text"


def test_combined_section_alias_and_block_alias() -> None:
    ref = _single("[[Note#Heading|Alias]]")
    assert ref.target == "Note"
    assert ref.section == "Heading"
    assert ref.display == "Alias"

    ref2 = _single("[[Note^block id|shown]]")
    assert ref2.target == "Note"
    assert ref2.block == "block id"
    assert ref2.display == "shown"


def test_web_link_kind() -> None:
    for url in ("https://example.com/x", "http://x.y", "ftp://files"):
        ref = _single(f"[[{url}]]")
        assert ref.kind == "web"
        assert ref.target == url
        assert ref.broken is False and ref.resolved_path is None


def test_self_heading_empty_target() -> None:
    ref = _single("[[#Heading]]")
    assert ref.target == ""
    assert ref.section == "Heading"
    assert ref.kind == "wikilink"


def test_multiple_links_in_one_text() -> None:
    refs = parse_wikilinks("[[a]] then [[b|c]] and ![[d]]")
    assert [r.target for r in refs] == ["a", "b", "d"]
    assert refs[1].display == "c"


def test_fenced_code_blocks_are_skipped() -> None:
    text = "outside [[real]]\n```python\n[[not-a-link]]\n```\ntail [[also-real]]"
    refs = parse_wikilinks(text)
    assert [r.target for r in refs] == ["real", "also-real"]


def test_unterminated_fence_skips_to_eof() -> None:
    text = "before [[yes]]\n```py\n[[nope]]\nstill code [[nope2]]"
    refs = parse_wikilinks(text)
    assert [r.target for r in refs] == ["yes"]


def test_tilde_fence_is_skipped() -> None:
    text = "before [[yes]]\n~~~\n[[nope]]\n~~~\nafter [[no2]]"
    refs = parse_wikilinks(text)
    assert [r.target for r in refs] == ["yes", "no2"]


def test_inline_code_is_skipped() -> None:
    text = "a `[[code]]` b [[real]] c ``[[double]]`` d"
    refs = parse_wikilinks(text)
    assert [r.target for r in refs] == ["real"]


def test_chinese_emoji_and_space_targets() -> None:
    ref = _single("看 [[中文 笔记|显示名]] 与 😀")
    assert ref.target == "中文 笔记"
    assert ref.display == "显示名"


def test_empty_inner_is_ignored() -> None:
    assert parse_wikilinks("[[ ]] text") == []
    assert parse_wikilinks("[[]] text") == []


def test_context_line_is_capped_and_single_line() -> None:
    long_line = "word " * 60 + "[[x]] tail"
    ref = _single(long_line)
    assert ref.context is not None
    assert "\n" not in ref.context
    assert len(ref.context) <= 200


def test_scanner_does_not_raise_on_junk() -> None:
    for junk in ("", "no links", "[[", "]]", "[[a]]" * 1000, "```\n[[x]]"):
        parse_wikilinks(junk)
