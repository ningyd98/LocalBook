"""M4 full-rebuild matrix (PLAN-M4 §5.5/§5.6/§9.1).

Covers: whole-Vault rescan counts, DTO stability, single-note failure
isolation, transaction atomicity (a mid-build failure rolls back and leaves
the previous consistent index), and build-state handling on listing failure.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from server.index.errors import IndexUnavailable
from server.index.service import DerivedIndexService
from server.search.service import SearchService
from server.vault.service import VaultService


def test_rebuild_counts_and_dto(
    vault_fixture_copy: Path,
    vault_service_factory: Callable[..., VaultService],
    index_service_factory: Callable[..., DerivedIndexService],
) -> None:
    service = vault_service_factory(vault_fixture_copy)
    index = index_service_factory(service)
    assert index.build_state == "ready"
    # bytes/non-utf8.md is the single unreadable note; the rest index fine.
    assert index.failed_count == 1
    assert index.skipped_count == 0
    assert index.note_count() > 10
    payload = index.rebuild()
    assert set(payload.model_dump()) == {
        "indexed",
        "skipped",
        "failed",
        "duration_ms",
        "ready",
        "generated_at",
    }
    assert payload.ready is True
    assert payload.indexed == index.note_count() - payload.failed
    assert payload.failed == index.failed_count
    assert payload.duration_ms >= 0.0


def test_rebuild_database_file_created_inside_localnote(
    vault_fixture_copy: Path,
    vault_service_factory: Callable[..., VaultService],
    index_service_factory: Callable[..., DerivedIndexService],
) -> None:
    service = vault_service_factory(vault_fixture_copy)
    index = index_service_factory(service)
    assert index.db_path is not None
    assert index.db_path.parent == service.root / ".localnote"
    assert index.db_path.name == "index.db"
    assert index.db_path.exists()
    assert index.db_path.stat().st_size > 0


def test_rebuild_is_queryable_via_sqlite(
    vault_fixture_copy: Path,
    vault_service_factory: Callable[..., VaultService],
    index_service_factory: Callable[..., DerivedIndexService],
) -> None:
    service = vault_service_factory(vault_fixture_copy)
    index = index_service_factory(service)
    entry = index.entry("中文 note.md")
    assert entry is not None
    assert entry.title == "中文 note"
    assert "你好世界" in entry.text
    assert entry.tags == ["中文标签", "工作"]
    # properties survived the JSON round trip through SQLite
    assert entry.properties.get("emoji") == "😀"
    assert entry.properties.get("related") == ["项目A", "项目B"]


def test_unreadable_note_is_isolated_not_fatal(
    vault_fixture_copy: Path,
    vault_service_factory: Callable[..., VaultService],
    index_service_factory: Callable[..., DerivedIndexService],
) -> None:
    service = vault_service_factory(vault_fixture_copy)
    index = index_service_factory(service)
    bad = index.entry("bytes/non-utf8.md")
    assert bad is not None
    assert bad.frontmatter_status == "unreadable"
    assert bad.diagnostic == "File is not valid UTF-8"
    assert bad.text == ""
    # every other note is searchable and healthy
    ok = index.entry("notes/a.md")
    assert ok is not None and ok.diagnostic is None
    assert SearchService(index).search("Ref").total >= 1


def test_rebuild_failure_rolls_back_transaction(
    vault_service_factory: Callable[..., VaultService],
    index_service_factory: Callable[..., DerivedIndexService],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    service = vault_service_factory(root)
    service.create_bytes("a.md", b"# A\npersistent body\n")
    service.create_bytes("b.md", b"# B\nother body\n")
    index = index_service_factory(service)
    before = {e.path: e.text for e in index.entries()}
    assert "persistent body" in before["a.md"]

    # Make the second file blow up mid-build: the transaction must roll back
    # so the previous consistent rows stay visible.
    calls: list[str] = []
    original = index._read_and_build_entry

    def failing(path, full_map, noext_map):  # type: ignore[no-untyped-def]
        calls.append(path)
        if path == "b.md":
            raise RuntimeError("simulated mid-build failure")
        return original(path, full_map, noext_map)

    monkeypatch.setattr(index, "_read_and_build_entry", failing)
    with pytest.raises(RuntimeError):
        index.rebuild()
    after = {e.path: e.text for e in index.entries()}
    assert after == before  # rollback left the previous rows intact
    # state stays whatever it was before the failed build (still queryable)
    assert index.build_state == "ready"
    assert "b.md" in calls


def test_listing_failure_marks_index_unavailable(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    service = vault_service_factory(root)
    service.create_bytes("a.md", b"# A\n")
    index = DerivedIndexService(service)
    index.rebuild()
    assert index.build_state == "ready"

    from server.vault.errors import VaultUnavailable

    def boom(*args: object, **kwargs: object) -> object:
        raise VaultUnavailable("root vanished")

    monkeypatch.setattr(service, "list_tree", boom)
    with pytest.raises(IndexUnavailable):
        index.rebuild()
    assert index.build_state == "unavailable"
    with pytest.raises(IndexUnavailable):
        index.assert_ready()


def test_rebuild_updates_after_external_change(
    vault_fixture_copy: Path,
    vault_service_factory: Callable[..., VaultService],
    index_service_factory: Callable[..., DerivedIndexService],
) -> None:
    service = vault_service_factory(vault_fixture_copy)
    index = index_service_factory(service)
    assert index.entry("notes/new.md") is None
    (service.root / "notes" / "new.md").write_bytes(b"# New file\nfresh-search-term-zqx\n")
    result = index.rebuild()
    assert result.ready is True
    assert index.entry("notes/new.md") is not None
    paths = [hit.path for hit in SearchService(index).search("fresh-search-term-zqx").hits]
    assert "notes/new.md" in paths
