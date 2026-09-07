"""M4 SQLite derived-library schema, versioning and migrations (PLAN-M4 §5.1/§5.2).

This module is pure declaration: no connections are opened here.  It defines

- ``SCHEMA_VERSION`` — the current schema version (``1`` for the M4 first
  release);
- the ``notes``/``tags``/``properties``/``links``/``backlinks`` DDL (the
  ``notes_fts`` FTS5 virtual table is created separately by
  :mod:`server.index.db` because its availability depends on the running
  SQLite build);
- ``MIGRATIONS[current_version] -> [sql, ...]``, the ordered, idempotent
  upgrade scripts that move a database from ``current_version`` to
  ``current_version + 1``.  ``MIGRATIONS[0]`` bootstraps a fresh database to
  version 1 and is the only migration a v1 schema ever runs.

The M7 ``ai_jobs``/``ai_job_journal`` History tables intentionally do NOT
live here: they are ensured separately by
:meth:`server.index.db.IndexDatabase._ensure_history_tables`, whose single
DDL source is :mod:`server.history.schema` (additive to a v1 database, never
part of the M4 version chain).

Everything stored here is *derived data*: deleting ``.localnote/index.db``
and re-scanning the Vault must reproduce the same rows.  The authoritative
note bytes always live in the Vault files behind ``VaultService``.
"""

from __future__ import annotations

SCHEMA_VERSION = 1

# Column notes (additive refinements of the PLAN-M4 §5.1 sketch, recorded in
# the M4 dev report):
# - ``notes.search_folded`` is a casefolded search corpus (basename + title +
#   folded tags + body) so the degraded substring path runs in SQL with the
#   same Python-``casefold`` semantics M3 used;
# - ``tags.seq`` / ``links.seq`` preserve frontmatter/document order for DTO
#   fidelity;
# - ``links.candidates_json`` preserves ambiguous-ref candidate lists.
# Each list element is EXACTLY ONE SQL statement: sqlite3 ``execute`` cannot
# run scripts, and the migration runner wraps each version step in one
# transaction.

MIGRATIONS: dict[int, list[str]] = {
    0: [
        # notes main row (text/search_folded are derived copies, rebuildable)
        """
        CREATE TABLE IF NOT EXISTS notes (
          path               TEXT PRIMARY KEY,
          sha256             TEXT NOT NULL,
          title              TEXT NOT NULL,
          basename           TEXT NOT NULL,
          text               TEXT NOT NULL DEFAULT '',
          text_truncated     INTEGER NOT NULL DEFAULT 0,
          frontmatter_status TEXT NOT NULL DEFAULT 'none',
          diagnostic         TEXT,
          parse_error_json   TEXT,
          search_folded      TEXT NOT NULL DEFAULT ''
        )
        """,
        # tags (display + casefold key; seq keeps frontmatter order)
        """
        CREATE TABLE IF NOT EXISTS tags (
          note_path   TEXT NOT NULL REFERENCES notes(path) ON DELETE CASCADE,
          tag         TEXT NOT NULL,
          tag_folded  TEXT NOT NULL,
          seq         INTEGER NOT NULL DEFAULT 0,
          PRIMARY KEY (note_path, tag_folded)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_tags_folded ON tags(tag_folded)",
        # properties (frontmatter unknown fields, value kept as JSON)
        """
        CREATE TABLE IF NOT EXISTS properties (
          note_path   TEXT NOT NULL REFERENCES notes(path) ON DELETE CASCADE,
          key         TEXT NOT NULL,
          value_json  TEXT NOT NULL,
          PRIMARY KEY (note_path, key)
        )
        """,
        # links outgoing edges (seq = document order)
        """
        CREATE TABLE IF NOT EXISTS links (
          source_path     TEXT NOT NULL REFERENCES notes(path) ON DELETE CASCADE,
          seq             INTEGER NOT NULL DEFAULT 0,
          target          TEXT NOT NULL,
          raw             TEXT NOT NULL,
          kind            TEXT NOT NULL,
          display         TEXT,
          section         TEXT,
          block           TEXT,
          resolved_path   TEXT,
          broken          INTEGER NOT NULL DEFAULT 0,
          ambiguous       INTEGER NOT NULL DEFAULT 0,
          context         TEXT,
          candidates_json TEXT,
          PRIMARY KEY (source_path, seq)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_links_source ON links(source_path)",
        "CREATE INDEX IF NOT EXISTS idx_links_resolved ON links(resolved_path)",
        # backlinks reverse index (materialised; source follows notes CASCADE)
        """
        CREATE TABLE IF NOT EXISTS backlinks (
          target_path  TEXT NOT NULL,
          source_path  TEXT NOT NULL REFERENCES notes(path) ON DELETE CASCADE,
          context      TEXT,
          PRIMARY KEY (target_path, source_path)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_backlinks_target ON backlinks(target_path)",
    ],
}


def build_fts_ddl(tokenizer: str) -> str:
    """Return the FTS5 ``notes_fts`` DDL for ``tokenizer``.

    Redundant-content FTS table (PLAN-M4 §5.4): it stores its own copy of the
    searchable text, so snippets can be produced from ``notes.text`` inside the
    same database.  ``path`` is ``UNINDEXED`` and aligned with ``notes.rowid``
    so delete/join are O(1) lookups instead of full scans.
    """
    tokenizer = tokenizer.replace("'", "")
    return (
        "CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(\n"
        "  path UNINDEXED,\n"
        "  title,\n"
        "  basename,\n"
        "  tags,\n"
        "  body,\n"
        "  tokenize = '{tokenizer}'\n"
        ")".format(tokenizer=tokenizer or "unicode61")
    )


__all__ = ["MIGRATIONS", "SCHEMA_VERSION", "build_fts_ddl"]
