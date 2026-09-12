"""RAG derived tables for ``.localnote/index.db`` (M14 §五).

The tables live in the same SQLite database as the M4/FTS and M7 history data,
so they inherit its WAL/transaction/migration discipline. They are created
additively via :func:`ensure_rag_tables` — exactly like the M7 history tables —
so opening an existing database never forces a note re-index and the M4
``SCHEMA_VERSION`` stays stable.

Nothing here is a source of truth: dropping these tables (or the whole
``.localnote`` directory) loses only derived data and is always recoverable
from the Markdown files.
"""

from __future__ import annotations

import sqlite3

# Regenerating this records the *shape* of the derived RAG data (chunker +
# schema), not the embedding model. It is stored in ``rag_index_state`` so a
# future chunker/schema change can invalidate stale rows explicitly.
RAG_SCHEMA_VERSION = 1

_DOCUMENTS_DDL = """
CREATE TABLE IF NOT EXISTS rag_documents (
    path              TEXT PRIMARY KEY,
    document_id       TEXT NOT NULL,
    sha256            TEXT NOT NULL,
    content_hash      TEXT NOT NULL,
    chunk_count       INTEGER NOT NULL DEFAULT 0,
    embedded_count    INTEGER NOT NULL DEFAULT 0,
    embedding_model   TEXT,
    embedding_version TEXT,
    embedding_dimension INTEGER,
    status            TEXT NOT NULL DEFAULT 'ready',
    diagnostic        TEXT,
    modified_at       TEXT,
    indexed_at        TEXT
)
"""

_CHUNKS_DDL = """
CREATE TABLE IF NOT EXISTS rag_chunks (
    chunk_id      TEXT PRIMARY KEY,
    document_id   TEXT NOT NULL,
    path          TEXT NOT NULL,
    chunk_index   INTEGER NOT NULL,
    content       TEXT NOT NULL,
    content_hash  TEXT NOT NULL,
    heading       TEXT,
    heading_path  TEXT,
    section_path  TEXT,
    start_line    INTEGER NOT NULL,
    end_line      INTEGER NOT NULL,
    start_offset  INTEGER NOT NULL,
    end_offset    INTEGER NOT NULL,
    tags          TEXT NOT NULL DEFAULT '',
    aliases       TEXT NOT NULL DEFAULT '',
    token_estimate INTEGER NOT NULL DEFAULT 0,
    embedding_text TEXT NOT NULL
)
"""

_CHUNK_INDEXES_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_rag_chunks_document "
    "ON rag_chunks (document_id)",
    "CREATE INDEX IF NOT EXISTS idx_rag_chunks_path ON rag_chunks (path)",
)

_EMBEDDINGS_DDL = """
CREATE TABLE IF NOT EXISTS rag_embeddings (
    chunk_id      TEXT PRIMARY KEY,
    document_id   TEXT NOT NULL,
    content_hash  TEXT NOT NULL,
    model         TEXT NOT NULL,
    embedding_version TEXT NOT NULL DEFAULT '',
    dimension     INTEGER NOT NULL,
    vector        BLOB NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    FOREIGN KEY (chunk_id) REFERENCES rag_chunks (chunk_id) ON DELETE CASCADE
)
"""

_STATE_DDL = """
CREATE TABLE IF NOT EXISTS rag_index_state (
    id                  INTEGER PRIMARY KEY CHECK (id = 1),
    embedding_provider  TEXT NOT NULL DEFAULT '',
    embedding_model     TEXT NOT NULL DEFAULT '',
    embedding_dimension INTEGER NOT NULL DEFAULT 0,
    embedding_version   TEXT NOT NULL DEFAULT '',
    rag_schema_version  INTEGER NOT NULL DEFAULT 0,
    indexed_documents   INTEGER NOT NULL DEFAULT 0,
    chunk_count         INTEGER NOT NULL DEFAULT 0,
    pending             INTEGER NOT NULL DEFAULT 0,
    failed              INTEGER NOT NULL DEFAULT 0,
    status              TEXT NOT NULL DEFAULT 'empty',
    last_indexed_at     TEXT,
    updated_at          TEXT
)
"""

RAG_DDL_STATEMENTS: tuple[str, ...] = (
    _DOCUMENTS_DDL,
    _CHUNKS_DDL,
    *_CHUNK_INDEXES_DDL,
    _EMBEDDINGS_DDL,
    _STATE_DDL,
    "INSERT OR IGNORE INTO rag_index_state (id) VALUES (1)",
)

FTS_DDL = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS rag_chunks_fts USING fts5("
    "content, heading, tags, path, "
    "chunk_id UNINDEXED, document_id UNINDEXED, "
    "tokenize = '{tokenizer}')"
)

RAG_TABLE_NAMES: tuple[str, ...] = (
    "rag_embeddings",
    "rag_chunks_fts",
    "rag_chunks",
    "rag_documents",
    "rag_index_state",
)


def build_chunk_fts_ddl(tokenizer: str = "unicode61") -> str:
    """FTS5 DDL for the chunk index, with a validated tokenizer name."""
    safe = "".join(char for char in str(tokenizer) if char.isalnum() or char == "_")
    return FTS_DDL.format(tokenizer=safe or "unicode61")


def ensure_rag_tables(conn: sqlite3.Connection, *, tokenizer: str = "unicode61") -> bool:
    """Create the additive RAG tables; return whether chunk FTS5 is available.

    Mirrors ``server.history.schema.ensure_history_tables``: safe to call on
    every open, never destructive, and never bumps ``SCHEMA_VERSION``. A SQLite
    build without FTS5 degrades to the document-level FTS/substring keyword
    path instead of failing.
    """
    for statement in RAG_DDL_STATEMENTS:
        conn.execute(statement)
    try:
        conn.execute(build_chunk_fts_ddl(tokenizer))
    except sqlite3.OperationalError:
        return False
    return True


def drop_rag_tables(conn: sqlite3.Connection) -> None:
    """Remove every RAG table (explicit 'delete the index' operation)."""
    for statement in (
        "DROP TABLE IF EXISTS rag_embeddings",
        "DROP TABLE IF EXISTS rag_chunks_fts",
        "DROP TABLE IF EXISTS rag_chunks",
        "DROP TABLE IF EXISTS rag_documents",
        "DROP TABLE IF EXISTS rag_index_state",
    ):
        conn.execute(statement)


__all__ = [
    "RAG_DDL_STATEMENTS",
    "RAG_SCHEMA_VERSION",
    "RAG_TABLE_NAMES",
    "build_chunk_fts_ddl",
    "drop_rag_tables",
    "ensure_rag_tables",
]
