"""M4 query-read matrix (PLAN-M4 M4-08/§9.1): entry/outgoing/backlinks/tags
now come from SQLite with the same shapes M3 returned in memory.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from server.index.errors import IndexUnavailable
from server.index.service import DerivedIndexService
from server.links.service import LinksService
from server.vault.errors import PathNotFound
from server.vault.service import VaultService


def test_outgoing_rows_match_m3_semantics(
    vault_fixture_copy: Path,
    vault_service_factory: Callable[..., VaultService],
    index_service_factory: Callable[..., DerivedIndexService],
) -> None:
    index = index_service_factory(vault_service_factory(vault_fixture_copy))
    combo = index.entry("combo.md")
    assert combo is not None
    # document order is preserved through links.seq
    kinds = [(ref.raw, ref.kind) for ref in combo.outgoing]
    assert kinds[0] == ("[[Plain]]", "wikilink")
    assert kinds[1] == ("![[image.png]]", "embed")
    assert any(
        ref.raw == "[[https://example.com/page]]" and ref.kind == "web"
        for ref in combo.outgoing
    )
    self_anchors = [ref for ref in combo.outgoing if ref.target == ""]
    assert len(self_anchors) == 1
    assert self_anchors[0].section == "SelfHeading"
    assert self_anchors[0].resolved_path == "combo.md"  # self-anchor
    # code spans / fences were skipped by the parser before reaching SQLite
    assert all(
        ref.raw not in ("[[inline skipped]]", "[[fenced skipped]]")
        for ref in combo.outgoing
    )


def test_outgoing_for_missing_note_is_empty_list(
    vault_service_factory: Callable[..., VaultService],
    index_service_factory: Callable[..., DerivedIndexService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    vault = vault_service_factory(root)
    vault.create_bytes("a.md", b"# A\nsee [[x]]\n")
    index = index_service_factory(vault)
    assert index.outgoing_for("a.md") != []
    assert index.outgoing_for("missing.md") == []


def test_backlink_sources_are_sorted_and_deduped_by_source(
    vault_fixture_copy: Path,
    vault_service_factory: Callable[..., VaultService],
    index_service_factory: Callable[..., DerivedIndexService],
) -> None:
    index = index_service_factory(vault_service_factory(vault_fixture_copy))
    sources = index.backlink_sources("中文 note.md")
    paths = [s for s, _ in sources]
    assert "combo.md" in paths and "notes/Ref A.md" in paths
    # deterministic sort by (source casefold, raw)
    keys = [(s.casefold(), ref.raw) for s, ref in sources]
    assert keys == sorted(keys)


def test_tags_for_returns_casefolded_key_matches_sorted(
    vault_fixture_copy: Path,
    vault_service_factory: Callable[..., VaultService],
    index_service_factory: Callable[..., DerivedIndexService],
) -> None:
    index = index_service_factory(vault_service_factory(vault_fixture_copy))
    holders = index.tags_for("工作")
    assert "中文 note.md" in holders
    assert "frontmatter/unknown-fields.md" in holders
    # deterministic sorted result
    assert index.tags_for("工作") == sorted(holders, key=str.casefold)
    assert index.tags_for("nope-tag") == []


def test_entry_roundtrips_parse_error_and_properties(
    vault_fixture_copy: Path,
    vault_service_factory: Callable[..., VaultService],
    index_service_factory: Callable[..., DerivedIndexService],
) -> None:
    index = index_service_factory(vault_service_factory(vault_fixture_copy))
    bad = index.entry("frontmatter/bad-yaml.md")
    assert bad is not None
    assert bad.frontmatter_status == "parse_error"
    assert bad.parse_error is not None and bad.parse_error.get("kind") == "yaml"
    props = index.entry("frontmatter/unknown-fields.md")
    assert props is not None
    assert props.properties["custom_unknown"] == {"nested": [1, 2, "x"]}
    assert props.properties["count"] == 3


def test_entry_and_entries_agree(
    vault_fixture_copy: Path,
    vault_service_factory: Callable[..., VaultService],
    index_service_factory: Callable[..., DerivedIndexService],
) -> None:
    index = index_service_factory(vault_service_factory(vault_fixture_copy))
    all_entries = {entry.path: entry for entry in index.entries()}
    for path, entry in all_entries.items():
        single = index.entry(path)
        assert single is not None
        assert single.title == entry.title
        assert single.text == entry.text
        assert single.tags == entry.tags
        assert [r.raw for r in single.outgoing] == [r.raw for r in entry.outgoing]
    assert index.note_count() == len(all_entries)


def test_links_service_404_when_missing_note(
    vault_fixture_copy: Path,
    vault_service_factory: Callable[..., VaultService],
    index_service_factory: Callable[..., DerivedIndexService],
) -> None:
    index = index_service_factory(vault_service_factory(vault_fixture_copy))
    links = LinksService(index)
    with pytest.raises(PathNotFound):
        links.outgoing("does-not-exist.md")
    with pytest.raises(PathNotFound):
        links.backlinks("does-not-exist.md")


def test_clear_gates_queries_until_rebuild(
    vault_fixture_copy: Path,
    vault_service_factory: Callable[..., VaultService],
    index_service_factory: Callable[..., DerivedIndexService],
) -> None:
    index = index_service_factory(vault_service_factory(vault_fixture_copy))
    index.clear()
    assert index.build_state == "idle"
    with pytest.raises(IndexUnavailable):
        index.assert_ready()
    result = index.rebuild()
    assert result.ready is True
    assert index.build_state == "ready"
    assert index.entry("notes/a.md") is not None


def test_index_rows_are_plain_sqlite_data(
    vault_fixture_copy: Path,
    vault_service_factory: Callable[..., VaultService],
    index_service_factory: Callable[..., DerivedIndexService],
) -> None:
    """White-box: query the actual index.db to prove reads come from SQLite."""
    index = index_service_factory(vault_service_factory(vault_fixture_copy))
    assert index._db is not None
    row = index._db.fetchone(
        "SELECT title, basename, sha256 FROM notes WHERE path = 'notes/a.md'"
    )
    assert row is not None and row["basename"] == "a"
    tag_row = index._db.fetchone(
        "SELECT tag FROM tags WHERE note_path = 'notes/a.md' ORDER BY seq LIMIT 1"
    )
    assert tag_row["tag"] == "alpha"
    link_row = index._db.fetchone(
        "SELECT target, resolved_path, broken FROM links "
        "WHERE source_path = 'notes/a.md' AND target = 'Ref A'"
    )
    assert link_row["resolved_path"] == "notes/Ref A.md"
    assert link_row["broken"] == 0
