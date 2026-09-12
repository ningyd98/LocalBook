"""SQLite vector store (M14 §二/§五).

One file, no server: chunk rows, embeddings (float32 BLOBs) and index state all
live in ``.localnote/index.db`` next to the M4/FTS and M7 history tables. The
store speaks only to SQLite — it never reads the Vault, so it can never corrupt
a note.

Vector layout: one padded run of little-endian float32 values per *document*
(column ``vec_d<dim>``), laid out in ``chunk_index`` order. Stored vectors are
L2-normalised, so similarity is a plain dot product. Search unpacks one BLOB per
document rather than one per chunk, which keeps a pure-Python scan in the low
tens of milliseconds at the target scale (~5k–20k chunks) and avoids adding an
optional native dependency to every installation. A future ``sqlite-vec``
backend can implement the same :class:`~server.rag.vector.base.VectorStore`
protocol without touching retrieval, context building or the API.
"""

from __future__ import annotations

import logging
import math
import sqlite3
import struct
from collections.abc import Iterable, Sequence
from datetime import datetime

from ...index.db import DatabaseUnavailable, IndexDatabase, IndexDatabaseError
from ..schema import RAG_SCHEMA_VERSION, ensure_rag_tables
from ..schemas import RAGChunk, RAGIndexState
from .base import VectorHit, iter_vectors
from .kernels import dot_products, kernel_info

logger = logging.getLogger("localnote.rag.vector")

_CHUNK_COLUMNS = (
    "chunk_id, document_id, path, chunk_index, content, content_hash, heading, "
    "heading_path, section_path, start_line, end_line, start_offset, end_offset, "
    "tags, aliases, token_estimate"
)


class RagStoreUnavailable(RuntimeError):
    """The RAG derived tables cannot be opened (never a Vault failure)."""


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _tags_to_text(tags: Sequence[str]) -> str:
    return " ".join(str(tag) for tag in tags)


def _text_to_tags(text: str | None) -> list[str]:
    return [part for part in (text or "").split(" ") if part]


class SqliteVectorStore:
    """SQLite implementation of the :class:`VectorStore` protocol."""

    name = "sqlite"

    def __init__(self, db: IndexDatabase) -> None:
        self._db = db
        self._vector_dimension = 0
        self._embedding_column = ""
        self._fts_available = False
        self._opened = False
        self._id_cache: dict[str, list[str]] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def ensure_ready(self) -> None:
        if self._opened:
            return
        try:
            if not self._db.opened:
                self._db.open()
        except (DatabaseUnavailable, IndexDatabaseError) as exc:
            raise RagStoreUnavailable("RAG index database is unavailable") from exc
        try:
            connection = self._db._require_connection()
            self._fts_available = ensure_rag_tables(
                connection, tokenizer=self._db.fts_tokenizer
            )
            self._opened = True
            state = self._read_state()
        except sqlite3.Error as exc:
            raise RagStoreUnavailable("RAG derived tables are unavailable") from exc
        if state.embedding_dimension > 0:
            self._vector_dimension = int(state.embedding_dimension)
            self._embedding_column = self._column_for_dimension(
                self._vector_dimension
            )

    @property
    def fts_available(self) -> bool:
        return self._fts_available

    @property
    def db_path(self):
        return self._db.path

    def close(self) -> None:
        self._opened = False

    # ------------------------------------------------------------------
    # Embedding column
    # ------------------------------------------------------------------

    @staticmethod
    def _column_for_dimension(dimension: int) -> str:
        return f"vec_d{int(dimension)}"

    def _ensure_embedding_column(self, dimension: int) -> str:
        column = self._column_for_dimension(dimension)
        if column == self._embedding_column:
            return column
        if self._embedding_column:
            # Another dimension means every stored vector is meaningless:
            # drop the derived vectors and switch columns.
            logger.warning(
                "rag embedding dimension changed %s -> %s; clearing stored vectors",
                self._vector_dimension,
                dimension,
            )
            previous = self._embedding_column

            def clear(conn: sqlite3.Connection) -> None:
                conn.execute(f"UPDATE rag_documents SET {previous} = NULL")
                conn.execute("UPDATE rag_documents SET embedded_count = 0")
                conn.execute("DELETE FROM rag_embeddings")

            self._run_transaction(clear)
        try:
            self._db.execute(f"ALTER TABLE rag_documents ADD COLUMN {column} BLOB")
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc).lower():
                raise
        self._vector_dimension = int(dimension)
        self._embedding_column = column
        return column

    def _column_exists(self, column: str) -> bool:
        rows = self._fetchall("PRAGMA table_info(rag_documents)")
        return any(str(row["name"]) == column for row in rows)

    def _run_transaction(self, body) -> None:
        try:
            with self._db.transaction() as conn:
                body(conn)
        except sqlite3.Error as exc:  # pragma: no cover - sqlite level failure
            logger.warning("rag sqlite transaction failed: %s", exc)
            raise RagStoreUnavailable("RAG index write failed") from exc

    # ------------------------------------------------------------------
    # Document/chunk writes
    # ------------------------------------------------------------------

    def document_hash(self, path: str) -> str | None:
        row = self._fetchone(
            "SELECT sha256 FROM rag_documents WHERE path = ?", (path,)
        )
        return str(row["sha256"]) if row is not None else None

    def document_content_hash(self, path: str) -> str | None:
        row = self._fetchone(
            "SELECT content_hash FROM rag_documents WHERE path = ?", (path,)
        )
        return str(row["content_hash"]) if row is not None else None

    def upsert_chunks(
        self,
        chunks: Sequence[RAGChunk],
        *,
        sha256: str | None = None,
        content_hash: str | None = None,
    ) -> None:
        """Atomically replace every chunk row of the documents in ``chunks``."""
        if not chunks:
            return
        self.ensure_ready()
        documents: dict[str, list[RAGChunk]] = {}
        for chunk in chunks:
            documents.setdefault(chunk.document_id, []).append(chunk)

        def body(conn: sqlite3.Connection) -> None:
            for document_id, group in documents.items():
                ordered = sorted(group, key=lambda item: item.chunk_index)
                keep = [chunk.chunk_id for chunk in ordered]
                # Prune only the chunks that disappeared. Inserting a *new* set
                # of rows would otherwise cascade the vectors of unchanged
                # chunks away and force a full re-embedding of the document.
                if keep:
                    placeholders = ",".join("?" for _ in keep)
                    conn.execute(
                        "DELETE FROM rag_chunks WHERE document_id = ? "
                        f"AND chunk_id NOT IN ({placeholders})",
                        [document_id, *keep],
                    )
                else:
                    conn.execute(
                        "DELETE FROM rag_chunks WHERE document_id = ?", (document_id,)
                    )
                conn.execute(
                    "DELETE FROM rag_embeddings WHERE document_id = ? "
                    "AND chunk_id NOT IN (SELECT chunk_id FROM rag_chunks)",
                    (document_id,),
                )
                if self._fts_available and keep:
                    placeholders = ",".join("?" for _ in keep)
                    conn.execute(
                        "DELETE FROM rag_chunks_fts WHERE document_id = ? "
                        f"AND chunk_id NOT IN ({placeholders})",
                        [document_id, *keep],
                    )
                elif self._fts_available:
                    conn.execute(
                        "DELETE FROM rag_chunks_fts WHERE document_id = ?",
                        (document_id,),
                    )
                for chunk in ordered:
                    conn.execute(
                        "INSERT INTO rag_chunks (chunk_id, document_id, path, "
                        "chunk_index, content, content_hash, heading, heading_path, "
                        "section_path, start_line, end_line, start_offset, "
                        "end_offset, tags, aliases, token_estimate, embedding_text) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                        "ON CONFLICT(chunk_id) DO UPDATE SET "
                        "document_id=excluded.document_id, path=excluded.path, "
                        "chunk_index=excluded.chunk_index, content=excluded.content, "
                        "content_hash=excluded.content_hash, "
                        "heading=excluded.heading, "
                        "heading_path=excluded.heading_path, "
                        "section_path=excluded.section_path, "
                        "start_line=excluded.start_line, "
                        "end_line=excluded.end_line, "
                        "start_offset=excluded.start_offset, "
                        "end_offset=excluded.end_offset, tags=excluded.tags, "
                        "aliases=excluded.aliases, "
                        "token_estimate=excluded.token_estimate, "
                        "embedding_text=excluded.embedding_text",
                        (
                            chunk.chunk_id,
                            chunk.document_id,
                            chunk.path,
                            int(chunk.chunk_index),
                            chunk.content,
                            chunk.content_hash,
                            chunk.heading,
                            chunk.heading_path,
                            chunk.section_path,
                            int(chunk.start_line),
                            int(chunk.end_line),
                            int(chunk.start_offset),
                            int(chunk.end_offset),
                            _tags_to_text(chunk.tags),
                            _tags_to_text(chunk.aliases),
                            int(chunk.token_estimate),
                            chunk.embedding_text,
                        ),
                    )
                    if self._fts_available:
                        # Regular FTS5 tables accept no UNIQUE constraint, so an
                        # explicit delete keeps the mirror row-for-row identical
                        # to rag_chunks (a re-inserted chunk id must not double).
                        conn.execute(
                            "DELETE FROM rag_chunks_fts WHERE chunk_id = ?",
                            (chunk.chunk_id,),
                        )
                        conn.execute(
                            "INSERT INTO rag_chunks_fts (chunk_id, document_id, "
                            "path, heading, tags, content) VALUES (?,?,?,?,?,?)",
                            (
                                chunk.chunk_id,
                                chunk.document_id,
                                chunk.path,
                                " ".join(
                                    filter(
                                        None,
                                        [chunk.heading_path, chunk.section_path],
                                    )
                                ),
                                _tags_to_text(chunk.tags),
                                chunk.content,
                            ),
                        )
                first = group[0]
                conn.execute(
                    "INSERT INTO rag_documents (path, document_id, sha256, "
                    "content_hash, chunk_count, embedded_count, status, "
                    "modified_at, indexed_at) VALUES (?,?,?,?,?,0,'ready',?,?) "
                    "ON CONFLICT(path) DO UPDATE SET "
                    "sha256=excluded.sha256, content_hash=excluded.content_hash, "
                    "chunk_count=excluded.chunk_count, status='ready', "
                    "diagnostic=NULL, modified_at=excluded.modified_at, "
                    "indexed_at=excluded.indexed_at",
                    (
                        document_id,
                        document_id,
                        sha256 or document_id,
                        content_hash or first.content_hash,
                        len(group),
                        first.modified_at.isoformat() if first.modified_at else None,
                        _now_iso(),
                    ),
                )

        self._run_transaction(body)
        self._prune_fts()
        for path in documents:
            self._id_cache.pop(path, None)

    def delete_document(self, path: str) -> None:
        self.ensure_ready()

        def body(conn: sqlite3.Connection) -> None:
            self._delete_rows(conn, path)

        self._run_transaction(body)
        self._id_cache.pop(path, None)

    def rename_document(self, old_path: str, new_path: str) -> bool:
        """Re-point a document's derived rows without re-chunking/re-embedding.

        Returns True when rows existed for ``old_path``. The caller must verify
        that the *content* did not change: a rename combined with an edit goes
        through the normal modify path instead.
        """
        self.ensure_ready()
        if old_path == new_path:
            return True
        existing = self._fetchone(
            "SELECT chunk_count FROM rag_documents WHERE path = ?", (old_path,)
        )
        if existing is None:
            return False

        def body(conn: sqlite3.Connection) -> None:
            conn.execute(
                "UPDATE rag_documents SET path = ? WHERE path = ?",
                (new_path, old_path),
            )
            conn.execute(
                "UPDATE rag_chunks SET document_id = ?, path = ? WHERE document_id = ?",
                (new_path, new_path, old_path),
            )
            conn.execute(
                "UPDATE rag_embeddings SET document_id = ? WHERE document_id = ?",
                (new_path, old_path),
            )
            if self._fts_available:
                conn.execute(
                    "UPDATE rag_chunks_fts SET document_id = ?, path = ? "
                    "WHERE document_id = ?",
                    (new_path, new_path, old_path),
                )

        self._run_transaction(body)
        self._id_cache.pop(old_path, None)
        self._id_cache.pop(new_path, None)
        return True

    def _prune_fts(self) -> None:
        """Drop chunk-FTS rows whose chunk no longer exists."""
        if not self._fts_available:
            return
        self._run_transaction(
            lambda conn: conn.execute(
                "DELETE FROM rag_chunks_fts WHERE chunk_id NOT IN "
                "(SELECT chunk_id FROM rag_chunks)"
            )
        )

    def _delete_rows(self, conn: sqlite3.Connection, document_id: str) -> None:
        # Delete the chunks first: the FK cascade removes exactly the vectors of
        # the chunks that disappeared. Vectors of *unchanged* chunks survive a
        # document re-index, which is what makes a one-paragraph edit cost one
        # embedding instead of a whole note.
        conn.execute("DELETE FROM rag_chunks WHERE document_id = ?", (document_id,))
        conn.execute(
            "DELETE FROM rag_embeddings WHERE chunk_id NOT IN "
            "(SELECT chunk_id FROM rag_chunks)"
        )
        if self._fts_available:
            conn.execute(
                "DELETE FROM rag_chunks_fts WHERE document_id = ?", (document_id,)
            )
        conn.execute("DELETE FROM rag_documents WHERE path = ?", (document_id,))

    def upsert_embeddings(
        self,
        *,
        chunk_ids: Sequence[str],
        vectors: Sequence[Sequence[float]],
        model: str,
        embedding_version: str = "",
    ) -> None:
        """Store vectors for chunks and re-pack the affected vector runs."""
        if not chunk_ids:
            return
        self.ensure_ready()
        if len(chunk_ids) != len(vectors):
            raise ValueError("chunk_ids and vectors must have the same length")
        dimension = len(vectors[0])
        if any(len(vector) != dimension for vector in vectors):
            raise ValueError("all vectors in one batch must share a dimension")
        column = self._ensure_embedding_column(dimension)
        rows = self._details_by_chunk_id(chunk_ids)
        touched: set[str] = set()

        def body(conn: sqlite3.Connection) -> None:
            for chunk_id, vector in zip(chunk_ids, vectors, strict=True):
                row = rows.get(str(chunk_id))
                if row is None:
                    continue
                document_id = str(row["document_id"])
                touched.add(document_id)
                conn.execute(
                    "INSERT INTO rag_embeddings (chunk_id, document_id, "
                    "content_hash, model, embedding_version, dimension, vector) "
                    "VALUES (?,?,?,?,?,?,?) ON CONFLICT(chunk_id) DO UPDATE SET "
                    "model=excluded.model, "
                    "embedding_version=excluded.embedding_version, "
                    "dimension=excluded.dimension, vector=excluded.vector, "
                    "content_hash=excluded.content_hash",
                    (
                        str(chunk_id),
                        document_id,
                        str(row["content_hash"]),
                        model,
                        embedding_version,
                        dimension,
                        _pack(vector),
                    ),
                )
            for document_id in touched:
                self._repack_document(conn, document_id, column, dimension)

        self._run_transaction(body)
        if touched:
            self._run_transaction(
                lambda conn: conn.execute(
                    "DELETE FROM rag_embeddings WHERE chunk_id NOT IN "
                    "(SELECT chunk_id FROM rag_chunks)"
                )
            )

    def _repack_document(
        self,
        conn: sqlite3.Connection,
        document_id: str,
        column: str,
        dimension: int,
    ) -> None:
        rows = conn.execute(
            "SELECT c.chunk_id, e.vector FROM rag_chunks c "
            "JOIN rag_embeddings e ON e.chunk_id = c.chunk_id "
            "WHERE c.document_id = ? ORDER BY c.chunk_index",
            (document_id,),
        ).fetchall()
        run = bytearray()
        embedded = 0
        for row in rows:
            vector = row["vector"]
            if len(vector) != 4 * dimension:
                continue
            run.extend(vector)
            embedded += 1
        conn.execute(
            f"UPDATE rag_documents SET {column} = ?, embedded_count = ? "
            "WHERE path = ?",
            (bytes(run), embedded, document_id),
        )

    def set_document_sha256(self, path: str, sha256: str) -> None:
        """Record the Vault file digest without touching chunks or vectors.

        Used after a rename: the content is unchanged, so re-chunking (and
        re-embedding) would be pure waste.
        """
        self.ensure_ready()

        def body(conn: sqlite3.Connection) -> None:
            conn.execute(
                "UPDATE rag_documents SET sha256 = ?, indexed_at = ? WHERE path = ?",
                (str(sha256), _now_iso(), path),
            )

        self._run_transaction(body)

    def mark_documents_pending(self, paths: Sequence[str], reason: str) -> None:
        if not paths:
            return
        self.ensure_ready()

        def body(conn: sqlite3.Connection) -> None:
            for path in paths:
                conn.execute(
                    "UPDATE rag_documents SET status = 'pending', diagnostic = ? "
                    "WHERE path = ?",
                    (str(reason)[:200], path),
                )

        self._run_transaction(body)

    def mark_document_failed(self, path: str, reason: str) -> None:
        self.ensure_ready()

        def body(conn: sqlite3.Connection) -> None:
            conn.execute(
                "UPDATE rag_documents SET status = 'failed', diagnostic = ? "
                "WHERE path = ?",
                (str(reason)[:200], path),
            )

        self._run_transaction(body)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def chunk(self, chunk_id: str) -> RAGChunk | None:
        row = self._fetchone(
            f"SELECT {_CHUNK_COLUMNS} FROM rag_chunks WHERE chunk_id = ?",
            (chunk_id,),
        )
        return self._chunk_from_row(row) if row is not None else None

    def chunks_for_document(self, document_id: str) -> list[RAGChunk]:
        rows = self._fetchall(
            f"SELECT {_CHUNK_COLUMNS} FROM rag_chunks WHERE document_id = ? "
            "ORDER BY chunk_index",
            (document_id,),
        )
        return [self._chunk_from_row(row) for row in rows]

    chunks_for_path = chunks_for_document

    def chunks_without_embeddings(
        self, *, limit: int = 256, model: str, embedding_version: str = ""
    ) -> list[RAGChunk]:
        """Chunks whose stored vector is missing or was built by other settings."""
        rows = self._fetchall(
            "SELECT c.chunk_id, c.document_id, c.path, c.chunk_index, c.content, "
            "c.content_hash, c.heading, c.heading_path, c.section_path, "
            "c.start_line, c.end_line, c.start_offset, c.end_offset, c.tags, "
            "c.aliases, c.token_estimate FROM rag_chunks c "
            "LEFT JOIN rag_embeddings e ON e.chunk_id = c.chunk_id "
            "WHERE e.chunk_id IS NULL OR e.model <> ? "
            "OR e.embedding_version <> ? OR e.content_hash <> c.content_hash "
            "ORDER BY c.path, c.chunk_index LIMIT ?",
            (model, embedding_version, max(1, int(limit))),
        )
        return [self._chunk_from_row(row) for row in rows]

    def existing_embeddings(
        self, chunk_ids: Sequence[str], *, model: str, embedding_version: str = ""
    ) -> dict[str, str]:
        """``chunk_id -> stored content_hash`` for reusable vectors only.

        A vector is reusable when it was produced from the same chunk text by
        the same model/embedding version; anything else must be recomputed.
        """
        if not chunk_ids:
            return {}
        rows = self._fetchall(
            "SELECT chunk_id, content_hash FROM rag_embeddings WHERE model = ? "
            "AND embedding_version = ? AND chunk_id IN ("
            + ",".join("?" for _ in chunk_ids)
            + ")",
            [model, embedding_version, *chunk_ids],
        )
        return {str(row["chunk_id"]): str(row["content_hash"]) for row in rows}

    def document_paths(self) -> list[str]:
        rows = self._fetchall("SELECT path FROM rag_documents ORDER BY path")
        return [str(row["path"]) for row in rows]

    def document_statuses(self) -> dict[str, str]:
        rows = self._fetchall("SELECT path, status FROM rag_documents")
        return {str(row["path"]): str(row["status"]) for row in rows}

    def chunk_count(self) -> int:
        return self._scalar("SELECT COUNT(*) FROM rag_chunks")

    def embedded_count(self) -> int:
        return self._scalar("SELECT COUNT(*) FROM rag_embeddings")

    def document_count(self) -> int:
        return self._scalar("SELECT COUNT(*) FROM rag_documents")

    def _chunk_from_row(self, row: sqlite3.Row) -> RAGChunk:
        return RAGChunk(
            chunk_id=str(row["chunk_id"]),
            document_id=str(row["document_id"]),
            path=str(row["path"]),
            content=str(row["content"]),
            content_hash=str(row["content_hash"]),
            heading=row["heading"],
            heading_path=row["heading_path"],
            section_path=row["section_path"],
            start_offset=int(row["start_offset"]),
            end_offset=int(row["end_offset"]),
            start_line=int(row["start_line"]),
            end_line=int(row["end_line"]),
            tags=_text_to_tags(row["tags"]),
            aliases=_text_to_tags(row["aliases"]),
            chunk_index=int(row["chunk_index"]),
            token_estimate=int(row["token_estimate"]),
        )

    # ------------------------------------------------------------------
    # Vector search
    # ------------------------------------------------------------------

    def search(
        self,
        vector: Sequence[float],
        *,
        top_k: int = 30,
        allowed_paths: Sequence[str] | None = None,
    ) -> list[VectorHit]:
        """Brute-force cosine scan over the stored documents.

        Stored vectors are normalised, so the score is a plain dot product. One
        padded run is kept per document, so the scan unpacks one BLOB per
        document instead of one per chunk; :mod:`server.rag.vector.kernels`
        picks numpy when it is installed and the pure-Python kernel otherwise,
        so the path works everywhere and gets faster when numpy happens to be
        present (see ``GET /rag/index/status`` for the active kernel).
        """
        if not vector or top_k <= 0:
            return []
        self.ensure_ready()
        dimension = len(vector)
        column = self._column_for_dimension(dimension)
        if not self._column_exists(column):
            return []
        try:
            rows = self._db.fetchall(
                f"SELECT path, {column} AS vectors FROM rag_documents "
                f"WHERE {column} IS NOT NULL "
                f"AND length({column}) >= {4 * dimension}"
            )
        except sqlite3.Error as exc:
            logger.warning("rag vector scan failed: %s", exc)
            return []

        allowed = set(allowed_paths) if allowed_paths is not None else None
        scored: list[tuple[float, str]] = []
        for row in rows:
            path = str(row["path"])
            if allowed is not None and path not in allowed:
                continue
            ids = self._chunk_ids_for(path)
            if not ids:
                continue
            # The kernel scores the document's whole vector run (numpy when it
            # happens to be installed, pure Python otherwise).
            for score, index in dot_products(row["vectors"], vector, dimension):
                if 0 <= index < len(ids):
                    scored.append((score, ids[index]))
        if not scored:
            return []
        scored.sort(key=lambda item: (-item[0], item[1]))
        selected = scored[: max(1, int(top_k))]
        details = self._details_by_chunk_id([chunk_id for _, chunk_id in selected])
        hits: list[VectorHit] = []
        for rank, (score, chunk_id) in enumerate(selected, start=1):
            row = details.get(chunk_id)
            if row is None:
                continue
            hits.append(self._hit_from_row(row, score, rank))
        return hits

    def _details_by_chunk_id(
        self, chunk_ids: Sequence[str]
    ) -> dict[str, sqlite3.Row]:
        if not chunk_ids:
            return {}
        rows = self._fetchall(
            "SELECT chunk_id, document_id, path, heading, heading_path, "
            "section_path, start_line, end_line, content, content_hash, tags "
            "FROM rag_chunks WHERE chunk_id IN ("
            + ",".join("?" for _ in chunk_ids)
            + ")",
            list(chunk_ids),
        )
        return {str(row["chunk_id"]): row for row in rows}

    @staticmethod
    def _hit_from_row(row: sqlite3.Row, score: float, rank: int) -> VectorHit:
        keys = row.keys()
        resolved = float(row["hits"]) if "hits" in keys else float(score)
        return VectorHit(
            chunk_id=str(row["chunk_id"]),
            path=str(row["path"]),
            score=resolved,
            rank=int(rank),
            heading=row["heading"],
            heading_path=row["heading_path"],
            section_path=row["section_path"],
            start_line=int(row["start_line"]),
            end_line=int(row["end_line"]),
            content=str(row["content"]),
            content_hash=str(row["content_hash"]),
            tags=_text_to_tags(row["tags"]),
        )

    def _chunk_ids_for(self, path: str) -> list[str]:
        if path in self._id_cache:
            return self._id_cache[path]
        rows = self._fetchall(
            "SELECT chunk_id FROM rag_chunks WHERE document_id = ? "
            "ORDER BY chunk_index",
            (path,),
        )
        ids = [str(row["chunk_id"]) for row in rows]
        if len(self._id_cache) > 512:
            self._id_cache.clear()
        self._id_cache[path] = ids
        return ids

    def invalidate_id_cache(self) -> None:
        self._id_cache = {}

    @staticmethod
    def _coerce_floats(value) -> list[float]:
        if value is None:
            return []
        raw = bytes(value) if isinstance(value, (bytes, bytearray)) else None
        if raw is not None:
            usable = len(raw) - (len(raw) % 4)
            if usable <= 0:
                return []
            return list(struct.unpack(f"<{usable // 4}f", raw[:usable]))
        try:
            return [float(item) for item in value]
        except (TypeError, ValueError):
            return []

    def kernel(self) -> str:
        """Name of the similarity kernel serving scans (``numpy`` | ``python``)."""
        return kernel_info().name

    def has_vectors(self, dimension: int | None = None) -> bool:
        resolved = int(dimension) if dimension else int(self._vector_dimension)
        if resolved <= 0:
            return False
        column = self._column_for_dimension(resolved)
        if not self._column_exists(column):
            return False
        return (
            self._scalar(
                f"SELECT COUNT(*) FROM rag_documents WHERE {column} IS NOT NULL"
            )
            > 0
        )

    # ------------------------------------------------------------------
    # Keyword search over chunks (used by KeywordRetriever)
    # ------------------------------------------------------------------

    def chunk_fts_available(self) -> bool:
        return self._fts_available

    def chunk_fts_search(self, match_expr: str, *, limit: int = 60) -> list[VectorHit]:
        """Ranked chunk-level FTS5 search (bm25, best first)."""
        if not match_expr or not self._fts_available:
            return []
        try:
            rows = self._db.fetchall(
                "SELECT c.chunk_id, c.path, c.heading, c.heading_path, "
                "c.section_path, c.start_line, c.end_line, c.content, "
                "c.content_hash, c.tags, "
                "bm25(rag_chunks_fts, 1.0, 3.0, 2.0, 1.0) AS score "
                "FROM rag_chunks_fts JOIN rag_chunks c "
                "ON c.chunk_id = rag_chunks_fts.chunk_id "
                "WHERE rag_chunks_fts MATCH ? ORDER BY score ASC LIMIT ?",
                (match_expr, max(1, int(limit))),
            )
        except sqlite3.OperationalError:
            logger.warning("rag chunk fts query failed expr=%r", match_expr)
            return []
        return [
            self._hit_from_row(row, round(0.0 - float(row["score"] or 0.0), 6), rank)
            for rank, row in enumerate(rows, start=1)
        ]

    def substring_chunk_search(
        self,
        terms: Sequence[str],
        *,
        limit: int = 60,
        min_coverage: float = float("inf"),
    ) -> list[VectorHit]:
        """Casefolded substring search over chunks (CJK/short-query safe).

        Matching rule, documented because it decides what the lexical fallback
        can find at all: every term must be matched, either **verbatim** or by
        **character-bigram coverage** of that term (``min_coverage``). Chinese is
        written without spaces, so a question such as
        哪篇笔记记录了我对插件运行时的设计 is a single 17-character run that will
        never appear verbatim; coverage lets 插件运行时 match it, while a note
        sharing four unrelated characters (4/16 bigrams = 0.25) stays below the
        threshold and cannot be cited as evidence.

        Scoring counts matched bigrams, so the best-covering chunk ranks first.
        """
        cleaned = [str(term).casefold().strip() for term in terms]
        cleaned = [term for term in cleaned if term]
        if not cleaned:
            return []
        coverage = max(0.0, min(1.0, float(min_coverage)))
        and_parts: list[str] = []
        params: list[str] = []
        score_parts: list[str] = []
        score_params: list[str] = []
        for term in cleaned:
            score, score_params_for_term = _gram_coverage_expression(term)
            score_parts.append(score)
            score_params.extend(score_params_for_term)
            threshold = len(score_params_for_term) * coverage
            # A very short term (1–2 characters) matching verbatim would let a
            # single common character through, so short terms must clear the
            # coverage bar as well.
            verbatim = "1 = 0" if len(term) < 3 else "instr(lower(c.content), ?) > 0"
            and_parts.append(f"({verbatim} OR {score} >= {threshold:.4f})")
            if len(term) >= 3:
                params.append(term)
            params.extend(score_params_for_term)
        sql = (
            "SELECT c.chunk_id, c.path, c.heading, c.heading_path, c.section_path, "
            "c.start_line, c.end_line, c.content, c.content_hash, c.tags, ("
            + " + ".join(score_parts)
            + ") AS hits FROM rag_chunks c WHERE ("
            + " AND ".join(and_parts)
            + ") ORDER BY hits DESC, c.path, c.chunk_index LIMIT ?"
        )
        rows = self._fetchall(sql, [*params, *score_params, max(1, int(limit))])
        return [
            self._hit_from_row(row, 0.0, rank)
            for rank, row in enumerate(rows, start=1)
        ]

    # ------------------------------------------------------------------
    # Whole-index operations
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Drop every derived RAG row (a rebuild starts from an empty store)."""
        self.ensure_ready()

        def body(conn: sqlite3.Connection) -> None:
            if self._fts_available:
                conn.execute("DELETE FROM rag_chunks_fts")
            conn.execute("DELETE FROM rag_embeddings")
            conn.execute("DELETE FROM rag_chunks")
            conn.execute("DELETE FROM rag_documents")
            conn.execute("UPDATE rag_index_state SET pending=0, failed=0")

        self._run_transaction(body)
        self.invalidate_id_cache()
        self._vector_dimension = 0
        self._embedding_column = ""

    def state(self) -> RAGIndexState:
        self.ensure_ready()
        return self._read_state()

    def _read_state(self) -> RAGIndexState:
        row = self._db.fetchone("SELECT * FROM rag_index_state WHERE id = 1")
        if row is None:
            return RAGIndexState()
        last_indexed = row["last_indexed_at"]
        try:
            parsed = (
                datetime.fromisoformat(str(last_indexed)) if last_indexed else None
            )
        except ValueError:
            parsed = None
        return RAGIndexState(
            embedding_provider=str(row["embedding_provider"] or ""),
            embedding_model=str(row["embedding_model"] or ""),
            embedding_dimension=int(row["embedding_dimension"] or 0),
            embedding_version=str(row["embedding_version"] or ""),
            indexed_documents=int(row["indexed_documents"] or 0),
            chunk_count=int(row["chunk_count"] or 0),
            last_indexed_at=parsed,
            status=str(row["status"] or "empty"),  # type: ignore[arg-type]
            pending=int(row["pending"] or 0),
            failed=int(row["failed"] or 0),
        )

    def save_state(self, state: RAGIndexState) -> None:
        self.ensure_ready()
        self._run_transaction(
            lambda conn: conn.execute(
                "UPDATE rag_index_state SET embedding_provider=?, embedding_model=?, "
                "embedding_dimension=?, embedding_version=?, rag_schema_version=?, "
                "indexed_documents=?, chunk_count=?, pending=?, failed=?, status=?, "
                "last_indexed_at=?, updated_at=? WHERE id = 1",
                (
                    state.embedding_provider,
                    state.embedding_model,
                    int(state.embedding_dimension),
                    state.embedding_version,
                    RAG_SCHEMA_VERSION,
                    int(state.indexed_documents),
                    int(state.chunk_count),
                    int(state.pending),
                    int(state.failed),
                    state.status,
                    state.last_indexed_at.isoformat() if state.last_indexed_at else None,
                    _now_iso(),
                ),
            )
        )

    def refresh_counts(self, *, persist: bool = True) -> RAGIndexState:
        """Recompute counters/status from the row tables.

        ``ready`` means every stored chunk has a vector built by the current
        embedding settings; anything else is ``pending`` (work outstanding) or
        ``failed`` (work outstanding *and* at least one document errored).
        """
        state = self._read_state()
        state.indexed_documents = self.document_count()
        state.chunk_count = self.chunk_count()
        state.pending = self._scalar(
            "SELECT COUNT(*) FROM rag_documents WHERE status = 'pending'"
        )
        state.failed = self._scalar(
            "SELECT COUNT(*) FROM rag_documents WHERE status = 'failed'"
        )
        missing = self._scalar(
            "SELECT COUNT(*) FROM rag_chunks c LEFT JOIN rag_embeddings e "
            "ON e.chunk_id = c.chunk_id WHERE e.chunk_id IS NULL"
        )
        if state.chunk_count == 0:
            state.status = "empty"
        elif missing == 0:
            state.status = "ready"
        elif state.failed:
            state.status = "failed"
        else:
            state.status = "pending"
        if persist:
            self.save_state(state)
        return state

    # ------------------------------------------------------------------
    # Low level helpers
    # ------------------------------------------------------------------

    def _fetchone(self, sql: str, params: Sequence = ()) -> sqlite3.Row | None:
        self.ensure_ready()
        try:
            return self._db.fetchone(sql, params)
        except sqlite3.Error as exc:
            logger.warning("rag sqlite read failed: %s", exc)
            return None

    def _fetchall(self, sql: str, params: Sequence = ()) -> list[sqlite3.Row]:
        self.ensure_ready()
        try:
            return self._db.fetchall(sql, params)
        except sqlite3.Error as exc:
            logger.warning("rag sqlite read failed: %s", exc)
            return []

    def _scalar(self, sql: str, params: Sequence = ()) -> int:
        row = self._fetchone(sql, params)
        return int(row[0]) if row is not None else 0

    def legacy_vector_scan(
        self, vector: Sequence[float], top_k: int
    ) -> list[VectorHit]:
        """Reference scan used to validate the fast path's ranking.

        Deliberately simple (one row per embedding, no per-document packing) so
        a test can assert the optimised scan returns the same order.
        """
        if not vector or top_k <= 0:
            return []
        dimension = len(vector)
        rows = self._fetchall(
            "SELECT e.chunk_id, e.vector FROM rag_embeddings e "
            "JOIN rag_chunks c ON c.chunk_id = e.chunk_id WHERE e.dimension = ?",
            (dimension,),
        )
        scored: list[tuple[float, str]] = []
        for row in rows:
            stored = self._coerce_floats(row["vector"])
            if len(stored) != dimension:
                continue
            score = math.fsum(a * b for a, b in zip(stored, vector, strict=True))
            scored.append((score, str(row["chunk_id"])))
        scored.sort(key=lambda item: (-item[0], item[1]))
        selected = scored[: max(1, int(top_k))]
        details = self._details_by_chunk_id([chunk_id for _, chunk_id in selected])
        return [
            self._hit_from_row(details[chunk_id], score, rank)
            for rank, (score, chunk_id) in enumerate(selected, start=1)
            if chunk_id in details
        ]


def _gram_coverage_expression(term: str) -> tuple[str, list[str]]:
    """SQL counting how many overlapping character bigrams of ``term`` match.

    Returns ``(expression, params)``; a short term degenerates to its own
    occurrence count.
    """
    if len(term) < 2:
        return (
            "(CASE WHEN instr(lower(c.content), ?) > 0 THEN 1 ELSE 0 END)",
            [term],
        )
    parts: list[str] = []
    params: list[str] = []
    for index in range(len(term) - 1):
        gram = term[index : index + 2]
        parts.append("(CASE WHEN instr(lower(c.content), ?) > 0 THEN 1 ELSE 0 END)")
        params.append(gram)
    return "(" + " + ".join(parts) + ")", params


def _pack(vector: Sequence[float]) -> bytes:
    values = [float(value) for value in vector]
    return struct.pack(f"<{len(values)}f", *values)


def iter_packed(blob: bytes, dimension: int) -> Iterable[memoryview]:
    """Expose :func:`iter_vectors` for callers holding a raw run."""
    return iter_vectors(blob, dimension)


__all__ = ["RagStoreUnavailable", "SqliteVectorStore"]
