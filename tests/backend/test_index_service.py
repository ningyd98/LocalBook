"""M3 DerivedIndexService matrix (PLAN-M3 §5.3/§9.1).

Uses only fixture copies inside tmp_path or throwaway roots.  The index never
touches ``.localnote`` and never writes files.
"""

from __future__ import annotations

import hashlib
import shutil
import threading
from collections.abc import Callable
from pathlib import Path

from server.index.service import DerivedIndexService
from server.vault.events import VaultEvent
from server.vault.service import VaultService


def _mkdirs(root: Path, relative: str) -> None:
    (root / relative).mkdir(parents=True, exist_ok=True)


def _basenames_of(index: DerivedIndexService) -> dict[str, list[str]]:
    with index._lock:  # introspection-only helper for tests
        return {key: list(value) for key, value in index._basenames_noext.items()}


def test_rebuild_indexes_all_markdown_and_counts_failures(
    vault_fixture_copy: Path,
    vault_service_factory: Callable[..., VaultService],
    index_service_factory: Callable[..., DerivedIndexService],
) -> None:
    service = vault_service_factory(vault_fixture_copy)
    index = index_service_factory(service)
    assert index.build_state == "ready"
    assert index.note_count() > 5
    # bytes/non-utf8.md is the one file that cannot be decoded.
    assert index.failed_count >= 1
    assert index.skipped_count >= 0
    assert index.failed_count + index.skipped_count <= 2

    entry = index.entry("中文 note.md")
    assert entry is not None
    assert entry.frontmatter_status == "ok"
    assert entry.tags == ["中文标签", "工作"]
    assert entry.title == "中文 note"
    assert "你好世界" in entry.text

    # unreadable note is still isolated and present as a diagnostic row
    bad = index.entry("bytes/non-utf8.md")
    assert bad is not None
    assert bad.frontmatter_status == "unreadable"
    assert bad.diagnostic == "File is not valid UTF-8"
    assert bad.outgoing == []


def test_rebuild_resolution_broken_and_ambiguous(
    vault_fixture_copy: Path,
    vault_service_factory: Callable[..., VaultService],
    index_service_factory: Callable[..., DerivedIndexService],
) -> None:
    service = vault_service_factory(vault_fixture_copy)
    index = index_service_factory(service)

    a = index.entry("notes/a.md")
    assert a is not None
    by_target = {ref.target: ref for ref in a.outgoing}
    assert by_target["Ref A"].resolved_path == "notes/Ref A.md"
    assert by_target["Ref A"].broken is False
    assert by_target["Cfg B"].broken is True
    assert by_target["Cfg B"].resolved_path is None
    assert by_target["image.png"].kind == "embed"
    assert by_target["image.png"].resolved_path == "attachments/image.png"
    # duplicate basename -> ambiguous with deterministic first candidate
    alpha = by_target["Alpha"]
    assert alpha.ambiguous is True
    assert alpha.candidates == ["dup/Alpha.md", "notes/Alpha.md"]
    assert alpha.resolved_path == "dup/Alpha.md"

    # chinese target resolves
    assert by_target["中文 note"].resolved_path == "中文 note.md"


def test_backlinks_are_derived_reverse(
    vault_fixture_copy: Path,
    vault_service_factory: Callable[..., VaultService],
    index_service_factory: Callable[..., DerivedIndexService],
) -> None:
    service = vault_service_factory(vault_fixture_copy)
    index = index_service_factory(service)
    sources = [source for source, _ in index.backlink_sources("notes/Ref A.md")]
    assert "notes/a.md" in sources
    # self anchors are excluded from backlinks
    assert "combo.md" not in [s for s, _ in index.backlink_sources("combo.md")]


def test_text_cap_truncates_body(
    vault_service_factory: Callable[..., VaultService],
    index_service_factory: Callable[..., DerivedIndexService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    service = vault_service_factory(root)
    service.create_bytes("big.md", ("# Big\n" + ("x" * 400)).encode())
    index = index_service_factory(service, note_text_cap=100)
    entry = index.entry("big.md")
    assert entry is not None
    assert entry.text_truncated is True
    assert len(entry.text) == 100


def test_remove_localnote_then_rebuild_is_identical(
    vault_fixture_copy: Path,
    vault_service_factory: Callable[..., VaultService],
    index_service_factory: Callable[..., DerivedIndexService],
) -> None:
    root = vault_fixture_copy
    service = vault_service_factory(root)
    index = index_service_factory(service)
    before_entries = {e.path: e.sha256 for e in index.entries()}
    before_hash = hashlib.sha256((root / "中文 note.md").read_bytes()).hexdigest()

    localnote = root / ".localnote"
    assert localnote.is_dir()
    shutil.rmtree(localnote)

    # Deleting .localnote must not change the rebuilt index or file hashes.
    result = index.rebuild()
    assert result.ready is True
    after_entries = {e.path: e.sha256 for e in index.entries()}
    assert after_entries == before_entries
    after_hash = hashlib.sha256((root / "中文 note.md").read_bytes()).hexdigest()
    assert after_hash == before_hash
    assert localnote.exists() is False  # index never recreates it


def test_incremental_create_modify_delete_move(
    vault_service_factory: Callable[..., VaultService],
    index_service_factory: Callable[..., DerivedIndexService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    service = vault_service_factory(root)
    service.create_bytes("target.md", b"# Target\n")
    index = index_service_factory(service)

    # create: a new note linking to target
    service.create_bytes("src.md", b"# Src\nsee [[Target]]\n")
    index.handle_event(VaultEvent.created("src.md"))
    index.flush_events()  # P1-3: process buffered events immediately
    assert index.entry("src.md") is not None
    sources = [s for s, _ in index.backlink_sources("target.md")]
    assert "src.md" in sources

    # modify: src now links nowhere; backlink must be removed
    _data, digest = service.read_bytes("src.md")
    service.write_bytes("src.md", b"# Src\nno links now\n", digest)
    index.handle_event(VaultEvent.modified("src.md"))
    index.flush_events()  # P1-3: process buffered events immediately
    assert index.entry("src.md").outgoing == []
    assert index.backlink_sources("target.md") == []

    # delete removes entry + backlink contributions + basenames
    service.create_bytes("gone.md", b"# Gone\nsee [[Target]]\n")
    index.handle_event(VaultEvent.created("gone.md"))
    index.flush_events()  # P1-3: process buffered events immediately
    _data2, digest2 = service.read_bytes("gone.md")
    service.delete_file("gone.md", digest2)
    index.handle_event(VaultEvent.deleted("gone.md"))
    index.flush_events()  # P1-3: process buffered events immediately
    assert index.entry("gone.md") is None
    assert index.basenames_noext("gone") == []

    # move: old path removed, new path indexed
    service.create_bytes("move-me.md", b"# MoveMe\nsee [[Target]]\n")
    index.handle_event(VaultEvent.created("move-me.md"))
    index.flush_events()  # P1-3: process buffered events immediately
    _data3, digest3 = service.read_bytes("move-me.md")
    service.move_file("move-me.md", "moved.md", digest3)
    index.handle_event(VaultEvent.moved("move-me.md", "moved.md"))
    index.flush_events()  # P1-3: process buffered events immediately
    assert index.entry("move-me.md") is None
    assert index.entry("moved.md") is not None
    moved = index.entry("moved.md")
    assert moved is not None
    assert moved.resolved_targets == {"target.md"}


def test_tags_map_updates_on_modify(
    vault_service_factory: Callable[..., VaultService],
    index_service_factory: Callable[..., DerivedIndexService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    service = vault_service_factory(root)
    service.create_bytes("t.md", b"---\ntags: [one]\n---\n# T\n")
    index = index_service_factory(service)
    assert "t.md" in index.tags_for("one")

    _data, digest = service.read_bytes("t.md")
    service.write_bytes("t.md", b"---\ntags: [two]\n---\n# T\n", digest)
    index.handle_event(VaultEvent.modified("t.md"))
    index.flush_events()  # P1-3: process buffered events immediately
    assert index.tags_for("one") == []
    assert "t.md" in index.tags_for("two")


def test_rebuild_serializes_with_queries_under_lock(
    vault_fixture_copy: Path,
    vault_service_factory: Callable[..., VaultService],
    index_service_factory: Callable[..., DerivedIndexService],
) -> None:
    service = vault_service_factory(vault_fixture_copy)
    index = index_service_factory(service)
    errors: list[BaseException] = []

    def reader() -> None:
        try:
            for _ in range(40):
                index.entries()
                index.entry("notes/a.md")
                index.backlink_sources("notes/Ref A.md")
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=reader) for _ in range(4)]
    for thread in threads:
        thread.start()
    index.rebuild()
    index.rebuild()
    for thread in threads:
        thread.join()
    assert errors == []
    assert index.build_state == "ready"


def test_directory_events_are_ignored(
    vault_service_factory: Callable[..., VaultService],
    index_service_factory: Callable[..., DerivedIndexService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    service = vault_service_factory(root)
    index = index_service_factory(service)
    index.handle_event(VaultEvent.created("dir", is_directory=True))
    index.flush_events()  # P1-3: process buffered events immediately
    index.handle_event(VaultEvent.deleted("dir", is_directory=True))
    index.flush_events()  # P1-3: process buffered events immediately
    assert index.build_state == "ready"
    assert index.note_count() == 0
