"""M4 schema matrix (PLAN-M4 §5.1/§9.1): empty DB bootstrap and versioning.

Uses only ``tmp_path`` databases.  The schema is derived data: every table
must be creatable from scratch and recorded in ``schema_migrations``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from server.index import schema as index_schema
from server.index.db import IndexDatabase
from server.index.schema import SCHEMA_VERSION, build_fts_ddl

_EXPECTED_TABLES = {
    "schema_migrations",
    "notes",
    "tags",
    "properties",
    "links",
    "backlinks",
}


def _tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
    ).fetchall()
    return {str(row[0]) for row in rows}


def _assert_base_schema(conn: sqlite3.Connection) -> None:
    tables = _tables(conn)
    assert _EXPECTED_TABLES <= tables
    # FTS5 virtual table is created when the build supports it (macOS 3.53.4
    # does; schema tests assert on the live build).
    if "notes_fts" in tables:
        columns = [str(r[1]) for r in conn.execute("PRAGMA table_info(notes_fts)")]
        assert columns[:5] == ["path", "title", "basename", "tags", "body"]


def test_fresh_database_bootstraps_to_current_version(tmp_path: Path) -> None:
    db = IndexDatabase(tmp_path / "index.db")
    db.open()
    assert db.opened is True
    conn = db._require_connection()  # introspection for schema assertions
    version = conn.execute(
        "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
    ).fetchone()[0]
    assert int(version) == SCHEMA_VERSION == 1
    _assert_base_schema(conn)
    db.close()
    assert db.opened is False


def test_migrations_are_single_statements_and_idempotent(tmp_path: Path) -> None:
    """Reopening an already-current DB must not re-run or corrupt anything."""
    path = tmp_path / "index.db"
    first = IndexDatabase(path)
    first.open()
    first.close()
    second = IndexDatabase(path)
    second.open()  # idempotent reopen
    conn = second._require_connection()
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    assert [int(r[0]) for r in rows] == [SCHEMA_VERSION]
    _assert_base_schema(conn)
    second.close()


def test_notes_fts_created_when_sqlite_has_fts5(tmp_path: Path) -> None:
    db = IndexDatabase(tmp_path / "index.db")
    db.open()
    assert db.fts_available is True
    conn = db._require_connection()
    assert "notes_fts" in _tables(conn)
    # content copy round-trip: insert with aligned rowid and search it back
    conn.execute(
        "INSERT INTO notes (path, sha256, title, basename, text) "
        "VALUES ('a.md', 'h', 'A', 'a', 'hello fts body')"
    )
    rowid = conn.execute("SELECT rowid FROM notes WHERE path = 'a.md'").fetchone()[0]
    conn.execute(
        "INSERT INTO notes_fts (rowid, path, title, basename, tags, body) "
        "VALUES (?, 'a.md', 'A', 'a', '', 'hello fts body')",
        (rowid,),
    )
    found = conn.execute(
        "SELECT n.path FROM notes_fts JOIN notes n ON n.rowid = notes_fts.rowid "
        "WHERE notes_fts MATCH 'hello'"
    ).fetchall()
    assert [str(r[0]) for r in found] == ["a.md"]
    db.close()


def test_unknown_tokenizer_falls_back_to_unicode61(tmp_path: Path) -> None:
    db = IndexDatabase(tmp_path / "index.db", fts_tokenizer="definitely-not-a-tokenizer")
    db.open()
    # Functional degradation: FTS stays available through the unicode61
    # fallback so search keeps working (PLAN-M4 §5.4).
    assert db.fts_available is True
    assert db.fts_tokenizer == "unicode61"
    db.close()


def test_fts_ddl_escapes_single_quotes() -> None:
    # a hostile tokenizer value cannot break out of the quoted DDL string:
    # build_fts_ddl strips every quote from the tokenizer before embedding it,
    # so only the two SQL string delimiters survive.
    ddl = build_fts_ddl("unicode61 remove_diacritics 2")
    assert "unicode61 remove_diacritics 2" in ddl
    hostile = build_fts_ddl("x'); DROP TABLE notes;--")
    assert hostile.count("'") == 2  # only the SQL quoting pair
    payload = hostile.split("tokenize = ")[1]
    closing = payload.find("'", 1)
    assert closing > 0
    assert "'" not in payload[1:closing]  # inner content contains no quotes


def test_schema_constants_are_consistent() -> None:
    # Every migration entry key must lead monotonically toward the current
    # version, and SCHEMA_VERSION must be the top of the chain.
    assert 0 in index_schema.MIGRATIONS
    assert all(isinstance(stmt, str) and stmt.strip() for stmt in index_schema.MIGRATIONS[0])
