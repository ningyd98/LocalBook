"""M4 incremental (watcher-event) matrix (PLAN-M4 §5.5/§9.1).

Events are applied as single-note SQLite upserts/deletes sharing one RLock
with rebuild.  Coverage: create/modify/delete/move, sha256-same skip, tags /
backlinks / FTS consistency, and non-markdown + directory event ignoring.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from server.index.service import DerivedIndexService
from server.search.service import SearchService
from server.vault.events import VaultEvent
from server.vault.service import VaultService


def _indexed(root: Path, service: VaultService) -> DerivedIndexService:
    from server.index.service import DerivedIndexService

    index = DerivedIndexService(service)
    result = index.rebuild()
    assert result.ready is True
    return index


def test_create_modify_delete_keep_rows_and_fts_consistent(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    service = vault_service_factory(root)
    index = _indexed(root, service)
    search = SearchService(index)

    # create
    service.create_bytes("one.md", b"# One\nhello incremental-world\n")
    index.handle_event(VaultEvent.created("one.md"))
    index.flush_events()  # P1-3: process buffered events immediately
    index.flush_events()  # P1-3: process buffered events immediately
    entry = index.entry("one.md")
    assert entry is not None and "hello incremental-world" in entry.text
    assert [h.path for h in search.search("incremental-world").hits] == ["one.md"]

    # modify: FTS rows follow the new text (old term gone, new term found)
    _data, digest = service.read_bytes("one.md")
    service.write_bytes("one.md", b"# One\nbrand-new-body-q7\n", digest)
    index.handle_event(VaultEvent.modified("one.md"))
    index.flush_events()  # P1-3: process buffered events immediately
    index.flush_events()  # P1-3: process buffered events immediately
    assert [h.path for h in search.search("incremental-world").hits] == []
    assert [h.path for h in search.search("brand-new-body-q7").hits] == ["one.md"]

    # delete
    _data2, digest2 = service.read_bytes("one.md")
    service.delete_file("one.md", digest2)
    index.handle_event(VaultEvent.deleted("one.md"))
    index.flush_events()  # P1-3: process buffered events immediately
    assert index.entry("one.md") is None
    assert search.search("brand-new-body-q7").total == 0
    assert index.note_count() == 0


def test_same_sha256_modify_is_skipped(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    service = vault_service_factory(root)
    service.create_bytes("a.md", b"# A\nstable content\n")
    index = _indexed(root, service)
    before = index.entry("a.md").sha256
    # No real file change, but an event arrives (touch-like duplicate event).
    index.handle_event(VaultEvent.modified("a.md"))
    index.flush_events()  # P1-3: process buffered events immediately
    assert index.entry("a.md") is not None
    assert index.entry("a.md").sha256 == before
    assert index.note_count() == 1


def test_tags_and_backlinks_follow_modify(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    service = vault_service_factory(root)
    service.create_bytes("target.md", b"# Target\n")
    index = _indexed(root, service)

    service.create_bytes("src.md", b"---\ntags: [one]\n---\n# Src\nsee [[Target]]\n")
    index.handle_event(VaultEvent.created("src.md"))
    index.flush_events()  # P1-3: process buffered events immediately
    assert "src.md" in index.tags_for("one")
    assert [s for s, _ in index.backlink_sources("target.md")] == ["src.md"]

    _data, digest = service.read_bytes("src.md")
    service.write_bytes(
        "src.md", b"---\ntags: [two]\n---\n# Src\nno links now\n", digest
    )
    index.handle_event(VaultEvent.modified("src.md"))
    index.flush_events()  # P1-3: process buffered events immediately
    assert index.tags_for("one") == []
    assert "src.md" in index.tags_for("two")
    assert index.backlink_sources("target.md") == []


def test_delete_removes_note_and_backlink_contributions(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    service = vault_service_factory(root)
    service.create_bytes("target.md", b"# Target\n")
    service.create_bytes("src.md", b"# Src\nsee [[Target]]\n")
    index = _indexed(root, service)
    assert [s for s, _ in index.backlink_sources("target.md")] == ["src.md"]

    _data, digest = service.read_bytes("src.md")
    service.delete_file("src.md", digest)
    index.handle_event(VaultEvent.deleted("src.md"))
    index.flush_events()  # P1-3: process buffered events immediately
    assert index.entry("src.md") is None
    assert index.backlink_sources("target.md") == []
    assert index.basenames_noext("src") == []


def test_move_is_delete_plus_create(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "sub").mkdir()
    service = vault_service_factory(root)
    service.create_bytes("target.md", b"# Target\n")
    service.create_bytes("old.md", b"# Old\nsee [[Target]]\n")
    index = _indexed(root, service)

    _data, digest = service.read_bytes("old.md")
    service.move_file("old.md", "sub/new.md", digest)
    index.handle_event(VaultEvent.moved("old.md", "sub/new.md"))
    index.flush_events()  # P1-3: process buffered events immediately
    assert index.entry("old.md") is None
    moved = index.entry("sub/new.md")
    assert moved is not None
    assert moved.basename == "new"
    assert [s for s, _ in index.backlink_sources("target.md")] == ["sub/new.md"]
    assert index.basenames_noext("old") == []
    assert "sub/new.md" in index.basenames_noext("new")


def test_directory_and_non_markdown_events_ignored(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    service = vault_service_factory(root)
    service.create_bytes("a.md", b"# A\n")
    index = _indexed(root, service)
    count = index.note_count()
    index.handle_event(VaultEvent.created("folder", is_directory=True))
    index.flush_events()  # P1-3: process buffered events immediately
    index.handle_event(VaultEvent.deleted("folder", is_directory=True))
    index.flush_events()  # P1-3: process buffered events immediately
    index.handle_event(VaultEvent.created("image.png"))
    index.flush_events()  # P1-3: process buffered events immediately
    index.handle_event(VaultEvent.modified("image.png"))
    index.flush_events()  # P1-3: process buffered events immediately
    index.handle_event(VaultEvent.deleted("image.png"))
    index.flush_events()  # P1-3: process buffered events immediately
    assert index.note_count() == count


def test_unreadable_created_via_event_is_isolated(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    service = vault_service_factory(root)
    index = _indexed(root, service)
    service.create_bytes("bad.md", b"\xff\xfe not utf8\n")
    index.handle_event(VaultEvent.created("bad.md"))
    index.flush_events()  # P1-3: process buffered events immediately
    entry = index.entry("bad.md")
    assert entry is not None
    assert entry.frontmatter_status == "unreadable"
    assert entry.diagnostic == "File is not valid UTF-8"
    # not searchable, but does not break the rest
    assert SearchService(index).search("not utf8").total == 0
    assert index.entry("bad.md").outgoing == []
