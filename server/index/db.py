"""M4 SQLite connection management for the derived index (PLAN-M4 §5.2/§5.3).

Design (single connection + caller-held RLock):

- One ``sqlite3.Connection`` with ``check_same_thread=False`` plus a
  ``threading.RLock`` owned by ``DerivedIndexService`` serialises every read
  and write.  The local Vault is a single-user scenario and M3 already used an
  RLock; WAL matters here mainly for crash safety and future multi-connection
  extension, not for concurrent throughput (PLAN-M4 §5.3).
- PRAGMAs: ``journal_mode=WAL``, ``synchronous=NORMAL``,
  ``foreign_keys=ON`` (enables the ``ON DELETE CASCADE`` clean-up of
  tags/properties/links/backlinks) and ``busy_timeout=5000``.
- ``transaction()`` is an explicit ``BEGIN IMMEDIATE`` / COMMIT / ROLLBACK
  context manager so ``rebuild()`` and per-note upserts are atomic.
- Versioning: on open the runner compares ``schema_migrations`` with
  :data:`server.index.schema.SCHEMA_VERSION`; older databases are upgraded
  step-by-step (each migration in its own transaction); databases from a
  *future* version, version-gapped or corrupt databases are **dropped and
  recreated** — index data is derived and rebuildable, note bytes never live
  here (PLAN-M4 §5.2).
- FTS5 availability is probed once at open time: when the running SQLite build
  cannot create the ``notes_fts`` virtual table, ``fts_available`` is False and
  search degrades to the M3 substring path.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from . import schema as index_schema

logger = logging.getLogger("localnote.index.db")

_DEFAULT_BUSY_TIMEOUT_MS = 5000

# Whitelists keep PRAGMA values injected from config out of arbitrary SQL.
_JOURNAL_MODES = {"DELETE", "TRUNCATE", "PERSIST", "MEMORY", "WAL", "OFF"}
_SYNCHRONOUS_MODES = {"OFF", "NORMAL", "FULL", "EXTRA"}
_DEFAULT_FTS_TOKENIZER = "unicode61"


class IndexDatabaseError(Exception):
    """Base class for derived-index database failures."""


class FutureSchemaVersion(IndexDatabaseError):
    """The on-disk database was created by a newer server version."""


class MigrationGapError(IndexDatabaseError):
    """The database version cannot reach SCHEMA_VERSION by the known scripts."""


class DatabaseUnavailable(IndexDatabaseError):
    """The database file cannot be opened/created (missing derived dir, ...)."""


def _unlink_quietly(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:  # pragma: no cover - best effort on exotic platforms
        logger.warning("could not remove derived database file %s", path.name)


class IndexDatabase:
    """Own the SQLite connection of one derived ``index.db`` file."""

    def __init__(
        self,
        path: str | Path,
        *,
        journal_mode: str = "WAL",
        synchronous: str = "NORMAL",
        busy_timeout_ms: int = _DEFAULT_BUSY_TIMEOUT_MS,
        fts_tokenizer: str = _DEFAULT_FTS_TOKENIZER,
    ) -> None:
        self.path = Path(path)
        self._journal_mode = str(journal_mode).upper()
        self._synchronous = str(synchronous).upper()
        self._busy_timeout_ms = max(1, int(busy_timeout_ms))
        self._fts_tokenizer = fts_tokenizer or _DEFAULT_FTS_TOKENIZER
        if self._journal_mode not in _JOURNAL_MODES:
            raise ValueError(f"unsupported journal_mode: {journal_mode!r}")
        if self._synchronous not in _SYNCHRONOUS_MODES:
            raise ValueError(f"unsupported synchronous: {synchronous!r}")
        self._conn: sqlite3.Connection | None = None
        self._fts_available = False

    # ------------------------------------------------------------------
    # Open / close / lifecycle
    # ------------------------------------------------------------------

    @property
    def opened(self) -> bool:
        return self._conn is not None

    @property
    def fts_available(self) -> bool:
        return self._fts_available

    @property
    def fts_tokenizer(self) -> str:
        return self._fts_tokenizer

    def _connect(self) -> sqlite3.Connection:
        # isolation_level=None -> autocommit outside explicit transactions, so
        # ``transaction()`` fully controls BEGIN IMMEDIATE/COMMIT/ROLLBACK.
        conn = sqlite3.connect(
            str(self.path),
            timeout=self._busy_timeout_ms / 1000.0,
            check_same_thread=False,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA journal_mode = {self._journal_mode}")
        conn.execute(f"PRAGMA synchronous = {self._synchronous}")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
        return conn

    def open(self) -> None:
        """Open ``index.db``, run migrations and probe FTS5 support.

        A file whose *content* is not a database (corrupt/foreign) is dropped
        and recreated from scratch — derived data is disposable.  Raises
        :class:`DatabaseUnavailable` only when the file genuinely cannot be
        created/opened (missing ``.localnote`` directory, permissions) and
        :class:`ValueError` for invalid PRAGMA values.
        """
        if self._conn is not None:
            return
        try:
            conn = self._connect()
        except sqlite3.DatabaseError as exc:
            # connect() succeeded but the first PRAGMA hit non-database
            # content: drop the corrupt derived file and try once from empty.
            logger.warning(
                "derived index db content unusable (%s) -> dropping and recreating",
                exc,
            )
            self._drop_files()
            try:
                conn = self._connect()
            except sqlite3.Error as exc2:
                raise DatabaseUnavailable(
                    "Derived index database cannot be opened"
                ) from exc2
        except sqlite3.Error as exc:
            # Cannot open at all (missing directory, permissions, ...).
            raise DatabaseUnavailable(
                "Derived index database cannot be opened"
            ) from exc
        try:
            conn = self._prepare(conn)
        except sqlite3.Error as exc:  # e.g. corrupt after retry
            try:
                conn.close()
            except sqlite3.Error:  # pragma: no cover - defensive
                pass
            raise DatabaseUnavailable(
                "Derived index database is unavailable"
            ) from exc
        self._conn = conn
        self._fts_available = self._probe_fts(conn)
        logger.info(
            "derived index db open path=%s version=%d fts=%s",
            self.path.name,
            index_schema.SCHEMA_VERSION,
            self._fts_available,
        )

    def _prepare(self, conn: sqlite3.Connection) -> sqlite3.Connection:
        """Migrate (or drop + recreate) the database, returning a usable conn."""
        try:
            self._migrate(conn)
            # M7 history tables are additive derived metadata. Keep the M4
            # schema version stable for existing consumers while ensuring old
            # index databases receive the tables without rebuilding notes.
            self._ensure_history_tables(conn)
            return conn
        except (FutureSchemaVersion, MigrationGapError) as exc:
            logger.warning(
                "derived index db version mismatch -> dropping and recreating (%s)",
                exc,
            )
        except sqlite3.DatabaseError as exc:
            logger.warning(
                "derived index db corrupt (%s) -> dropping and recreating", exc
            )
        try:
            conn.close()
        except sqlite3.Error:  # pragma: no cover
            pass
        self._drop_files()
        fresh = self._connect()
        self._migrate(fresh)
        return fresh

    def _drop_files(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            _unlink_quietly(Path(str(self.path) + suffix))

    def close(self) -> None:
        conn = self._conn
        self._conn = None
        self._fts_available = False
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:  # pragma: no cover - defensive
                logger.exception("derived index db close failed")

    def clear(self) -> None:
        """Close and remove the on-disk database (derived data is disposable)."""
        self.close()
        self._drop_files()

    # ------------------------------------------------------------------
    # Migration / versioning
    # ------------------------------------------------------------------

    def _current_version(self, conn: sqlite3.Connection) -> int:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "  version     INTEGER PRIMARY KEY,"
            "  applied_at  TEXT NOT NULL DEFAULT "
            "(strftime('%Y-%m-%dT%H:%M:%fZ','now'))"
            ")"
        )
        row = conn.execute(
            "SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations"
        ).fetchone()
        return int(row["version"])

    def _migrate(self, conn: sqlite3.Connection) -> None:
        current = self._current_version(conn)
        target = index_schema.SCHEMA_VERSION
        if current > target:
            raise FutureSchemaVersion(
                f"database schema v{current} is newer than server v{target}"
            )
        for version in range(current, target):
            statements = index_schema.MIGRATIONS.get(version)
            if not statements:
                raise MigrationGapError(
                    f"no migration script from schema v{version} "
                    f"to v{version + 1}"
                )
            conn.execute("BEGIN IMMEDIATE")
            try:
                for statement in statements:
                    conn.execute(statement)
                conn.execute(
                    "INSERT INTO schema_migrations (version) VALUES (?)",
                    (version + 1,),
                )
            except Exception:
                conn.execute("ROLLBACK")
                raise
            else:
                conn.execute("COMMIT")

    def _ensure_history_tables(self, conn: sqlite3.Connection) -> None:
        """Create additive M7 history tables without changing M4 versioning.

        Single DDL source of truth: :mod:`server.history.schema`
        (``ensure_history_tables``).  Imported lazily so that importing
        ``server.index.db`` never triggers the ``server.history`` package
        (whose repository imports this module) at module load time.
        """
        from server.history.schema import ensure_history_tables

        ensure_history_tables(conn)

    def _probe_fts(self, conn: sqlite3.Connection) -> bool:
        """Create ``notes_fts`` when the SQLite build supports it.

        A configured tokenizer that the build rejects falls back to
        ``unicode61`` (functionality first).  Returns False only when FTS5
        itself is unavailable; search then degrades to the substring path.
        """
        candidates = [self._fts_tokenizer, _DEFAULT_FTS_TOKENIZER]
        for tokenizer in dict.fromkeys(candidates):
            ddl = index_schema.build_fts_ddl(tokenizer)
            try:
                conn.execute(ddl)
            except sqlite3.OperationalError as exc:
                logger.warning(
                    "fts5 unavailable for tokenizer=%r (%s); trying fallback",
                    tokenizer,
                    exc,
                )
                continue
            if tokenizer != self._fts_tokenizer:
                logger.warning(
                    "fts5 tokenizer %r not supported; using %r",
                    self._fts_tokenizer,
                    tokenizer,
                )
                self._fts_tokenizer = tokenizer
            return True
        logger.warning(
            "fts5 is not available on this SQLite build; "
            "search will use the substring fallback path"
        )
        return False

    # ------------------------------------------------------------------
    # Execution helpers (caller holds the index RLock)
    # ------------------------------------------------------------------

    def _require_connection(self) -> sqlite3.Connection:
        if self._conn is None:
            raise IndexDatabaseError("Derived index database is not open")
        return self._conn

    def execute(self, sql: str, params: Any = ()) -> sqlite3.Cursor:
        """Execute one statement in autocommit mode (no implicit transaction)."""
        return self._require_connection().execute(sql, params)

    def fetchone(self, sql: str, params: Any = ()) -> sqlite3.Row | None:
        return self.execute(sql, params).fetchone()

    def fetchall(self, sql: str, params: Any = ()) -> list[sqlite3.Row]:
        return self.execute(sql, params).fetchall()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Run a block inside one ``BEGIN IMMEDIATE`` transaction.

        Commits on success and rolls back on any exception so a half-applied
        rebuild or upsert can never be observed.
        """
        conn = self._require_connection()
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:  # pragma: no cover - defensive
                logger.exception("derived index rollback failed")
            raise
        else:
            conn.execute("COMMIT")


__all__ = [
    "DatabaseUnavailable",
    "FutureSchemaVersion",
    "IndexDatabase",
    "IndexDatabaseError",
    "MigrationGapError",
]
