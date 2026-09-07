"""M5 additive index read helpers (PLAN-M5 §5.3/§9.1; task M5-03).

``DerivedIndexService`` only gains read-only methods here —
``note_exists``/``note_title``/``note_tags``/``graph_snapshot``/``graph_totals``
— with zero changes to the existing M1–M4 write path.  These tests pin the
helper semantics, their deterministic ordering and their conservative
failure behaviour.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from server.index.errors import IndexUnavailable
from server.index.service import DerivedIndexService
from server.vault.service import VaultService


@pytest.fixture
def index(
    vault_fixture_copy: Path,
    vault_service_factory: Callable[..., VaultService],
    index_service_factory: Callable[..., DerivedIndexService],
) -> DerivedIndexService:
    service = vault_service_factory(vault_fixture_copy)
    index = index_service_factory(service)
    return index


def test_note_exists_and_title(index: DerivedIndexService) -> None:
    assert index.note_exists("notes/a.md") is True
    assert index.note_exists("中文 note.md") is True
    assert index.note_exists("中文与 Unicode/😀 note.md") is True
    assert index.note_exists("notes/missing.md") is False
    # unreadable notes still have derived rows; they are notes, not ghosts
    assert index.note_exists("bytes/non-utf8.md") is True

    assert index.note_title("notes/a.md") == "A"
    assert index.note_title("中文 note.md") == "中文 note"
    assert index.note_title("notes/missing.md") is None


def test_note_tags_preserve_seq_order_and_folded_keys(index: DerivedIndexService) -> None:
    tags = index.note_tags("notes/a.md")
    assert tags == [("alpha", "alpha"), ("beta", "beta")]
    # frontmatter duplicate [alpha, beta, alpha] collapses to one row
    assert len(tags) == 2

    chinese = index.note_tags("中文 note.md")
    assert chinese == [("中文标签", "中文标签"), ("工作", "工作")]
    assert index.note_tags("notes/missing.md") == []


def test_graph_snapshot_ordering_and_totals(index: DerivedIndexService) -> None:
    snapshot = index.graph_snapshot()
    totals = index.graph_totals()

    paths = [note.path for note in snapshot.notes]
    # SQLite ORDER BY path is the canonical byte ordering (== node-ID order,
    # since percent-encoding preserves byte order); plain string sort matches.
    assert paths == sorted(paths)
    assert len(paths) == totals.note_count
    assert "notes/a.md" in paths and "中文 note.md" in paths

    tags = list(snapshot.tags)
    assert tags == sorted(tags, key=lambda row: (row.note_path, row.seq))
    folded = {row.tag_folded for row in tags}
    assert totals.tag_count == len(folded)
    assert {"alpha", "beta", "工作", "中文标签"} <= folded

    links = list(snapshot.links)
    assert links == sorted(links, key=lambda row: (row.source_path, row.seq))
    assert totals.link_count == len(links)
    assert any(row.raw == "[[Cfg B]]" and row.broken for row in links)
    assert any(row.kind == "web" for row in links)  # stored by M4, skipped by graph


def test_graph_snapshot_is_deterministic(index: DerivedIndexService) -> None:
    first = index.graph_snapshot()
    second = index.graph_snapshot()
    assert first == second
    assert index.graph_totals() == index.graph_totals()


def test_graph_helpers_are_read_only(index: DerivedIndexService) -> None:
    with index._lock:  # introspection under the same RLock (M4 test style)
        db = index._db
        assert db is not None
        counts_before = {
            table: int(db.fetchone(f"SELECT COUNT(*) AS n FROM {table}")["n"])
            for table in ("notes", "tags", "links", "backlinks", "properties")
        }
    index.graph_snapshot()
    index.graph_totals()
    index.note_exists("notes/a.md")
    index.note_title("notes/a.md")
    index.note_tags("notes/a.md")
    index.outgoing_for("notes/a.md")
    index.backlink_sources("notes/Ref A.md")
    with index._lock:
        db = index._db
        assert db is not None
        counts_after = {
            table: int(db.fetchone(f"SELECT COUNT(*) AS n FROM {table}")["n"])
            for table in ("notes", "tags", "links", "backlinks", "properties")
        }
    assert counts_after == counts_before


def test_graph_helpers_fail_conservatively_when_db_closed(index: DerivedIndexService) -> None:
    index.close()
    assert index.note_exists("notes/a.md") is False
    assert index.note_title("notes/a.md") is None
    assert index.note_tags("notes/a.md") == []
    with pytest.raises(IndexUnavailable):
        index.graph_snapshot()
    with pytest.raises(IndexUnavailable):
        index.graph_totals()
