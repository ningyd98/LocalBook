"""M4 connection/thread-safety matrix (PLAN-M4 §5.3/§9.1).

``IndexDatabase`` is the single-connection core; its RLock serialises SQLite
access across index, RAG, and history services. ``DerivedIndexService`` has a
separate RLock for its own multi-step state. These tests cover transactions,
PRAGMAs, close semantics, and cross-service concurrency.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path

import pytest

from server.index.db import IndexDatabase, IndexDatabaseError
from server.index.service import DerivedIndexService
from server.rag.vector import sqlite as vector_sqlite
from server.rag.vector.sqlite import SqliteVectorStore
from server.vault.service import VaultService


def test_transaction_commits_and_rolls_back(tmp_path: Path) -> None:
    db = IndexDatabase(tmp_path / "index.db")
    db.open()
    with db.transaction() as conn:
        conn.execute("CREATE TABLE t (x INTEGER)")
        conn.execute("INSERT INTO t VALUES (1)")
    assert db.fetchone("SELECT COUNT(*) AS n FROM t")["n"] == 1

    with pytest.raises(RuntimeError):
        with db.transaction() as conn:
            conn.execute("INSERT INTO t VALUES (2)")
            raise RuntimeError("boom")
    assert db.fetchone("SELECT COUNT(*) AS n FROM t")["n"] == 1
    db.close()


def test_shared_connection_serializes_transactions(tmp_path: Path) -> None:
    """RAG status and startup indexing must not BEGIN on the same connection."""
    db = IndexDatabase(tmp_path / "index.db")
    db.open()
    db.execute("CREATE TABLE concurrent_writes (value INTEGER)")
    first_entered = threading.Event()
    release_first = threading.Event()
    second_started = threading.Event()

    def first_write() -> None:
        with db.transaction() as conn:
            conn.execute("INSERT INTO concurrent_writes VALUES (1)")
            first_entered.set()
            assert release_first.wait(timeout=5)

    def second_write() -> None:
        second_started.set()
        with db.transaction() as conn:
            conn.execute("INSERT INTO concurrent_writes VALUES (2)")

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(first_write)
        assert first_entered.wait(timeout=5)
        second = pool.submit(second_write)
        assert second_started.wait(timeout=5)
        try:
            with pytest.raises(FutureTimeout):
                second.result(timeout=0.1)
        finally:
            release_first.set()
        first.result(timeout=5)
        second.result(timeout=5)
    values = db.fetchall("SELECT value FROM concurrent_writes ORDER BY value")
    assert [row["value"] for row in values] == [1, 2]
    db.close()


def test_shared_connection_reads_wait_for_transaction(tmp_path: Path) -> None:
    """A status read must not see another subsystem's uncommitted rows."""
    db = IndexDatabase(tmp_path / "index.db")
    db.open()
    db.execute("CREATE TABLE concurrent_reads (value INTEGER)")
    write_entered = threading.Event()
    release_write = threading.Event()
    read_started = threading.Event()

    def write() -> None:
        with db.transaction() as conn:
            conn.execute("INSERT INTO concurrent_reads VALUES (1)")
            write_entered.set()
            assert release_write.wait(timeout=5)

    def read() -> int:
        read_started.set()
        return int(db.fetchone("SELECT COUNT(*) AS n FROM concurrent_reads")["n"])

    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(write)
        assert write_entered.wait(timeout=5)
        reader = pool.submit(read)
        assert read_started.wait(timeout=5)
        try:
            with pytest.raises(FutureTimeout):
                reader.result(timeout=0.1)
        finally:
            release_write.set()
        writer.result(timeout=5)
        assert reader.result(timeout=5) == 1
    db.close()


def test_rag_schema_initialization_waits_for_index_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reconfiguring RAG must not run its DDL inside an index transaction."""
    db = IndexDatabase(tmp_path / "index.db")
    db.open()
    store = SqliteVectorStore(db)
    index_entered = threading.Event()
    release_index = threading.Event()
    rag_started = threading.Event()
    schema_started = threading.Event()
    original_ensure = vector_sqlite.ensure_rag_tables

    def track_schema(*args, **kwargs):
        schema_started.set()
        return original_ensure(*args, **kwargs)

    monkeypatch.setattr(vector_sqlite, "ensure_rag_tables", track_schema)

    def index_write() -> None:
        with db.transaction() as conn:
            conn.execute("CREATE TABLE concurrent_schema (value INTEGER)")
            index_entered.set()
            assert release_index.wait(timeout=5)

    def rag_open() -> None:
        rag_started.set()
        store.ensure_ready()

    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(index_write)
        assert index_entered.wait(timeout=5)
        rag = pool.submit(rag_open)
        assert rag_started.wait(timeout=5)
        try:
            assert not schema_started.wait(timeout=0.1)
            with pytest.raises(FutureTimeout):
                rag.result(timeout=0.1)
        finally:
            release_index.set()
        writer.result(timeout=5)
        rag.result(timeout=5)
    assert store.state().status == "empty"
    db.close()


def test_pragmas_are_applied(tmp_path: Path) -> None:
    db = IndexDatabase(tmp_path / "index.db")
    db.open()
    conn = db._require_connection()
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    # synchronous returns an int (0=OFF 1=NORMAL 2=FULL 3=EXTRA)
    assert int(conn.execute("PRAGMA synchronous").fetchone()[0]) == 1
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert int(conn.execute("PRAGMA busy_timeout").fetchone()[0]) == 5000
    db.close()


def test_configured_pragmas_respected(tmp_path: Path) -> None:
    db = IndexDatabase(
        tmp_path / "index.db",
        journal_mode="DELETE",
        synchronous="FULL",
        busy_timeout_ms=111,
    )
    db.open()
    conn = db._require_connection()
    assert int(conn.execute("PRAGMA synchronous").fetchone()[0]) == 2
    assert int(conn.execute("PRAGMA busy_timeout").fetchone()[0]) == 111
    db.close()


def test_close_blocks_further_use(tmp_path: Path) -> None:
    db = IndexDatabase(tmp_path / "index.db")
    db.open()
    db.close()
    with pytest.raises(IndexDatabaseError):
        db.execute("SELECT 1")


def test_invalid_pragma_values_rejected() -> None:
    with pytest.raises(ValueError):
        IndexDatabase("x.db", journal_mode="nope")
    with pytest.raises(ValueError):
        IndexDatabase("x.db", synchronous="nope")


def test_foreign_key_cascade_active(tmp_path: Path) -> None:
    """The plan relies on ON DELETE CASCADE for note cleanup (§5.1)."""
    db = IndexDatabase(tmp_path / "index.db")
    db.open()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO notes (path, sha256, title, basename) VALUES ('a.md','h','A','a')"
        )
        conn.execute(
            "INSERT INTO links (source_path, seq, target, raw, kind) "
            "VALUES ('a.md', 0, 'x', '[[x]]', 'wikilink')"
        )
        conn.execute(
            "INSERT INTO backlinks (target_path, source_path) VALUES ('t.md','a.md')"
        )
    with db.transaction() as conn:
        conn.execute("DELETE FROM notes WHERE path = 'a.md'")
    assert db.fetchone("SELECT COUNT(*) AS n FROM links")["n"] == 0
    assert db.fetchone("SELECT COUNT(*) AS n FROM backlinks")["n"] == 0
    db.close()


def test_concurrent_readers_and_rebuilds_are_serialized(
    vault_fixture_copy: Path,
    vault_service_factory: Callable[..., VaultService],
    index_service_factory: Callable[..., DerivedIndexService],
) -> None:
    """Watcher-ish threads + request threads share one RLock without BUSY."""
    service = vault_service_factory(vault_fixture_copy)
    index = index_service_factory(service)
    errors: list[BaseException] = []

    def reader() -> None:
        try:
            for _ in range(50):
                assert index.build_state == "ready"
                index.entries()
                index.entry("notes/a.md")
                index.backlink_sources("notes/Ref A.md")
                index.tags_for("工作")
                index.outgoing_for("combo.md")
        except BaseException as exc:  # pragma: no cover - report + fail
            errors.append(exc)

    def writer() -> None:
        try:
            for _ in range(3):
                result = index.rebuild()
                assert result.ready is True
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=reader) for _ in range(4)]
    threads.append(threading.Thread(target=writer))
    for thread in threads:
        thread.start()
    index.rebuild()
    index.rebuild()
    for thread in threads:
        thread.join(timeout=30)
    assert errors == []
    assert index.build_state == "ready"
    assert index.note_count() > 5
