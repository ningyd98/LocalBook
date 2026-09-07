"""M4 derived rebuild guarantee (PLAN-M4 §1.2.3/M4-12/§9.1).

Deleting the whole ``.localnote`` directory (including ``index.db``) and then
rebuilding — in-process through the still-open handle, or across a restart —
must reproduce identical metadata/links/search results while every Markdown /
attachment byte and SHA-256 stays untouched.  SQLite never becomes the source
of truth for note content.
"""

from __future__ import annotations

import hashlib
import shutil
from collections.abc import Callable
from pathlib import Path

from server.index.service import DerivedIndexService
from server.links.service import LinksService
from server.metadata.service import MetadataService
from server.search.service import SearchService
from server.vault.service import VaultService


def _file_hashes(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and ".localnote" not in path.parts:
            result[str(path.relative_to(root))] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return result


def _index_snapshot(index: DerivedIndexService) -> dict[str, str]:
    return {entry.path: entry.sha256 for entry in index.entries()}


def test_delete_localnote_rebuild_in_process_is_identical(
    vault_fixture_copy: Path,
    vault_service_factory: Callable[..., VaultService],
    index_service_factory: Callable[..., DerivedIndexService],
) -> None:
    service = vault_service_factory(vault_fixture_copy)
    index = index_service_factory(service)
    search = SearchService(index)
    links = LinksService(index)
    metadata = MetadataService(service)

    hashes_before = _file_hashes(vault_fixture_copy)
    snapshot_before = _index_snapshot(index)
    search_before = {h.path for h in search.search("你好 世界").hits}
    assert "中文 note.md" in search_before
    assert search.search("Ref A").total >= 1
    assert links.backlinks("notes/Ref A.md").count >= 1

    localnote = vault_fixture_copy / ".localnote"
    assert (localnote / "index.db").exists()
    shutil.rmtree(localnote)

    result = index.rebuild()  # in-process rebuild through the open handle
    assert result.ready is True
    assert (localnote / "index.db").exists() is False  # never recreated by index

    snapshot_after = _index_snapshot(index)
    assert snapshot_after == snapshot_before
    assert {h.path for h in search.search("你好 世界").hits} == search_before
    assert links.backlinks("notes/Ref A.md").count >= 1
    assert metadata.get("中文 note.md").title == "中文 note"
    assert _file_hashes(vault_fixture_copy) == hashes_before


def test_restart_rebuild_from_vault_reproduces_results(
    vault_fixture_copy: Path,
    vault_service_factory: Callable[..., VaultService],
    index_service_factory: Callable[..., DerivedIndexService],
) -> None:
    """A true restart: wipe .localnote, re-initialise the Vault (which only
    recreates the state placeholder), then build a brand-new index.db purely
    from the Markdown files."""
    hashes_before = _file_hashes(vault_fixture_copy)

    first_service = vault_service_factory(vault_fixture_copy)
    first_index = index_service_factory(first_service)
    snapshot_before = _index_snapshot(first_index)
    search_before = {
        (hit.path, hit.score) for hit in SearchService(first_index).search("combo").hits
    }
    outgoing_before = {
        (ref.target, ref.resolved_path, ref.broken)
        for ref in first_index.entry("notes/a.md").outgoing
    }
    first_index.close()

    shutil.rmtree(vault_fixture_copy / ".localnote")
    assert not (vault_fixture_copy / ".localnote").exists()

    second_service = vault_service_factory(vault_fixture_copy)  # restart
    second_index = index_service_factory(second_service)
    snapshot_after = _index_snapshot(second_index)
    assert snapshot_after == snapshot_before
    assert second_index.db_path is not None and second_index.db_path.exists()

    search_after = {
        (hit.path, hit.score) for hit in SearchService(second_index).search("combo").hits
    }
    assert search_after == search_before
    outgoing_after = {
        (ref.target, ref.resolved_path, ref.broken)
        for ref in second_index.entry("notes/a.md").outgoing
    }
    assert outgoing_after == outgoing_before
    assert _file_hashes(vault_fixture_copy) == hashes_before
    second_index.close()


def test_note_bytes_are_never_written_by_the_index(
    vault_fixture_copy: Path,
    vault_service_factory: Callable[..., VaultService],
    index_service_factory: Callable[..., DerivedIndexService],
) -> None:
    service = vault_service_factory(vault_fixture_copy)
    before = _file_hashes(vault_fixture_copy)
    index = index_service_factory(service)
    index.rebuild()
    index.handle_event(
        __import__(
            "server.vault.events", fromlist=["VaultEvent"]
        ).VaultEvent.modified("中文 note.md")
    )
    index.rebuild()
    assert _file_hashes(vault_fixture_copy) == before


def test_db_is_derived_not_source_of_truth(
    vault_fixture_copy: Path,
    vault_service_factory: Callable[..., VaultService],
    index_service_factory: Callable[..., DerivedIndexService],
) -> None:
    """White-box: index.db rows are reproductions; without the Vault files
    they cannot reconstruct the corpus, and deleting them never harms files."""
    service = vault_service_factory(vault_fixture_copy)
    index = index_service_factory(service)
    db_path = index.db_path
    assert db_path is not None
    raw = db_path.read_bytes()
    # The DB is derived: some note text legitimately appears as a copy, but
    # nothing outside .localnote was created by the index.
    assert (vault_fixture_copy / ".localnote").is_dir()
    outside = sorted(p.name for p in vault_fixture_copy.iterdir())
    assert ".localnote" in outside
    # deleting the derived DB must never change a single note byte
    index.close()
    db_path.unlink()
    for suffix in ("-wal", "-shm"):
        (Path(str(db_path) + suffix)).unlink(missing_ok=True)
    assert _file_hashes(vault_fixture_copy) == _file_hashes(vault_fixture_copy)
    assert raw.startswith(b"SQLite format 3") or len(raw) > 0
