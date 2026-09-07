"""M4 migration/versioning matrix (PLAN-M4 §5.2/§9.1).

The runner upgrades older databases step-by-step and *drops + recreates*
databases that are newer, version-gapped or corrupt — derived data may always
be rebuilt from the Vault.  Version behaviour is exercised by monkeypatching
``server.index.schema`` (db.py reads it at runtime).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from server.index import schema as index_schema
from server.index.db import IndexDatabase, IndexDatabaseError
from server.index.schema import SCHEMA_VERSION


def _open_plain(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def test_upgrade_v1_to_v2_applies_script_and_keeps_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "index.db"
    first = IndexDatabase(path)
    first.open()
    with first.transaction() as conn:
        conn.execute(
            "INSERT INTO notes (path, sha256, title, basename, text) "
            "VALUES ('keep.md', 'h', 'Keep', 'keep', 'body')"
        )
    first.close()

    # Simulate server v2 shipping migration script 1 -> 2.
    monkeypatch.setattr(index_schema, "SCHEMA_VERSION", 2)
    monkeypatch.setattr(
        index_schema,
        "MIGRATIONS",
        {
            **index_schema.MIGRATIONS,
            1: [
                "ALTER TABLE notes ADD COLUMN migrated INTEGER NOT NULL DEFAULT 0"
            ],
        },
    )
    second = IndexDatabase(path)
    second.open()
    conn = second._require_connection()
    version = conn.execute(
        "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
    ).fetchone()[0]
    assert int(version) == 2
    columns = [str(r[1]) for r in conn.execute("PRAGMA table_info(notes)")]
    assert "migrated" in columns
    # data written at v1 survived the upgrade
    row = conn.execute("SELECT title FROM notes WHERE path = 'keep.md'").fetchone()
    assert row is not None and row["title"] == "Keep"
    second.close()


def test_future_version_database_is_dropped_and_rebuilt(
    tmp_path: Path,
) -> None:
    path = tmp_path / "index.db"
    first = IndexDatabase(path)
    first.open()
    with first.transaction() as conn:
        conn.execute(
            "INSERT INTO notes (path, sha256, title, basename, text) "
            "VALUES ('a.md', 'h', 'A', 'a', 'x')"
        )
        conn.execute("INSERT INTO schema_migrations (version) VALUES (99)")
    first.close()

    # A database created by a newer server must be discarded, not half-migrated.
    reopened = IndexDatabase(path)
    reopened.open()
    conn = reopened._require_connection()
    versions = [int(r[0]) for r in conn.execute("SELECT version FROM schema_migrations")]
    assert versions == [SCHEMA_VERSION]
    assert conn.execute("SELECT COUNT(*) AS n FROM notes").fetchone()["n"] == 0
    reopened.close()


def test_corrupt_database_is_dropped_and_rebuilt(tmp_path: Path) -> None:
    path = tmp_path / "index.db"
    path.write_bytes(b"this is definitely not a sqlite database file \x00\x01")
    db = IndexDatabase(path)
    db.open()  # must not raise; corrupt derived data is disposable
    assert db.opened is True
    conn = db._require_connection()
    version = conn.execute(
        "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
    ).fetchone()[0]
    assert int(version) == SCHEMA_VERSION
    assert "notes" in {
        str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master")
    }
    db.close()


def test_version_gap_surfaces_error_and_leaves_no_half_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing migration script cannot be silently papered over.

    The runner tries the drop + recreate fallback once; when the gap also
    blocks the fresh path (0 -> current), the error is surfaced - a broken
    migration table is a server-build bug and must be loud.  Derived data
    means the file can always be deleted and rebuilt by hand.
    """
    from server.index.db import MigrationGapError

    path = tmp_path / "index.db"
    monkeypatch.setattr(index_schema, "SCHEMA_VERSION", 2)
    monkeypatch.setattr(index_schema, "MIGRATIONS", {})  # nothing can migrate
    db = IndexDatabase(path)
    with pytest.raises(MigrationGapError):
        db.open()
    monkeypatch.undo()

    # Once the (broken) migration table is restored, a fresh open works again.
    restored = IndexDatabase(path)
    restored.open()
    conn = restored._require_connection()
    versions = [
        int(r[0]) for r in conn.execute("SELECT version FROM schema_migrations")
    ]
    assert versions == [SCHEMA_VERSION]
    restored.close()


def test_missing_derived_directory_raises_database_unavailable(
    tmp_path: Path,
) -> None:
    from server.index.db import DatabaseUnavailable

    db = IndexDatabase(tmp_path / "no-such-dir" / "index.db")
    with pytest.raises(DatabaseUnavailable):
        db.open()


def test_broken_migration_step_is_loud_and_not_half_applied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing step inside one migration version rolls back atomically.

    The step-level transaction guarantee is exercised directly on the runner:
    the partial ``CREATE TABLE`` must be rolled back when a later statement in
    the same version fails, so no half-applied state can ever be observed.
    """
    import sqlite3


    path = tmp_path / "index.db"
    monkeypatch.setattr(
        index_schema,
        "MIGRATIONS",
        {
            0: [
                "CREATE TABLE partial_step (x INTEGER)",
                "THIS IS NOT VALID SQL",
            ]
        },
    )
    db = IndexDatabase(path)
    with pytest.raises((sqlite3.OperationalError, IndexDatabaseError)):
        db.open()
    monkeypatch.undo()

    # verify nothing half-applied survives: clean open recreates exactly v1
    clean = IndexDatabase(path)
    clean.open()
    conn = clean._require_connection()
    tables = {str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master")}
    assert "partial_step" not in tables
    versions = [
        int(r[0]) for r in conn.execute("SELECT version FROM schema_migrations")
    ]
    assert versions == [SCHEMA_VERSION]
    clean.close()
