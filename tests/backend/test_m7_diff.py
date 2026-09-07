"""M7 Diff/bytes fidelity tests (PLAN-M7 §9.1-4)."""

from __future__ import annotations

import pytest

from server.actions.diff import build_diff, sha256
from server.actions.patches import (
    UnsupportedPatch,
    add_link,
    add_tags,
    apply_hunks,
    has_tags_block,
    remove_tags,
    tags_only_change,
)
from server.actions.schemas import Action, ActionType, PatchHunk

UTF8_BOM = "\ufeff---\ntags:\n  - work\n---\n# Title\r\nline two\r\n".encode("utf-8")
CRLF_DOC = b"# Title\r\nbody line\r\n"
LF_DOC = b"# Title\nbody line\n"
NON_UTF8 = b"\xff\xfe\x00binary"


def test_sha256_stable_and_prefixed() -> None:
    digest = sha256(b"abc")
    assert digest.startswith("sha256:") and len(digest) == 7 + 64


def test_update_diff_preserves_bom_and_crlf() -> None:
    after = UTF8_BOM.replace(b"- work", b"- work\n  - inbox")
    entry = build_diff("notes/a.md", UTF8_BOM, after)
    assert entry.operation == "update"
    assert entry.before_hash == sha256(UTF8_BOM)
    assert entry.after_hash == sha256(after)
    assert entry.unified_diff is not None
    assert "- work" in entry.unified_diff


def test_create_and_delete_operation_kinds() -> None:
    created = build_diff("n.md", None, b"# x\n")
    assert created.operation == "create" and created.before_hash is None
    removed = build_diff("n.md", b"# x\n", None)
    assert removed.operation == "delete" and removed.after_hash is None


def test_non_utf8_diff_keeps_hash_but_no_unified_text() -> None:
    entry = build_diff("notes/blob.md", NON_UTF8, NON_UTF8 + b"more")
    assert entry.operation == "update"
    assert entry.unified_diff is None
    assert entry.before_hash == sha256(NON_UTF8)
    assert entry.after_hash == sha256(NON_UTF8 + b"more")


def test_diff_is_pure_metadata_no_writes(tmp_path: pytest.TempPathFactory) -> None:
    marker = tmp_path / "sentinel.txt"
    marker.write_text("x", encoding="utf-8")
    entry = build_diff("n.md", b"a\n", b"b\n")
    assert marker.read_text(encoding="utf-8") == "x"
    assert entry.status == "proposed"


def test_add_tags_preserves_unknown_fields_and_body() -> None:
    doc = "---\ntitle: T\ncustom:\n  deep: 1\ntags:\n  - work\n---\n# Body\n"
    out = add_tags(doc, ["inbox"])
    assert "- inbox" in out
    assert "title: T" in out and "deep: 1" in out
    assert out.endswith("# Body\n")
    assert tags_only_change(doc, out)


def test_add_tags_creates_frontmatter_when_absent() -> None:
    doc = "# no frontmatter\n"
    out = add_tags(doc, ["x"])
    assert out.startswith("---\ntags:\n  - x\n---\n# no frontmatter\n")


def test_add_tags_inserts_into_existing_frontmatter() -> None:
    doc = "---\ntitle: T\n---\n# Body\n"
    out = add_tags(doc, ["x"])
    assert "title: T" in out
    assert "- x" in out
    assert out.startswith("---\n")


def test_remove_tags_keeps_other_tags_and_removes_empty_block() -> None:
    doc = "---\ntags:\n  - keep\n  - drop\n---\n# Body\n"
    out = remove_tags(doc, ["drop"])
    assert "- keep" in out and "- drop" not in out
    out_empty = remove_tags(doc, ["keep", "drop"])
    assert "tags:" not in out_empty.split("---", 2)[1]
    assert "# Body" in out_empty


def test_remove_tags_unknown_tag_raises() -> None:
    with pytest.raises(UnsupportedPatch):
        remove_tags("---\ntags:\n  - a\n---\n", ["zzz"])


def test_remove_tags_preserves_bom_and_crlf() -> None:
    doc = "\ufeff---\r\ntags:\r\n  - a\r\n  - b\r\n---\r\nbody\r\n"
    out = remove_tags(doc, ["a"])
    assert "\ufeff" in out
    assert "- b" in out
    assert "body\r\n" in out
    assert "- a" not in out


def test_add_link_appends_and_rejects_open_fence() -> None:
    doc = "# T\n\nbody\n"
    out = add_link(doc, "notes/other")
    assert out.endswith("[[notes/other]]\n")
    with pytest.raises(UnsupportedPatch):
        add_link("# T\n\n```python\nprint(1)\n", "notes/other")


def test_apply_hunks_ordered_with_offset() -> None:
    data = b"aaa\nbbb\nccc\n"
    h1 = PatchHunk(start=0, old_text="aaa", new_text="AAA")
    h2 = PatchHunk(start=8, old_text="ccc", new_text="CCC")
    out = apply_hunks(data, [h1, h2])
    assert out == b"AAA\nbbb\nCCC\n"
    with pytest.raises(UnsupportedPatch):
        apply_hunks(data, [PatchHunk(start=0, old_text="missing", new_text="x")])


def test_tags_only_change_detects_body_mutations() -> None:
    before = "---\ntags:\n  - a\n---\nbody\n"
    after_tag = add_tags(before, ["b"])
    assert tags_only_change(before, after_tag)
    after_body = before.replace("body", "changed")
    assert not tags_only_change(before, after_body)


def test_has_tags_block() -> None:
    assert has_tags_block("---\ntags:\n  - a\n---\n")
    assert not has_tags_block("---\ntitle: T\n---\n")
    assert not has_tags_block("# plain\n")


def test_tag_action_reason_is_required_by_schema() -> None:
    with pytest.raises(Exception):
        Action(
            action=ActionType.ADD_TAGS,
            file="notes/a.md",
            tags=["x"],
            reason="",  # empty reason
        )
