"""RAG index service: chunk → embed → store, incrementally and safely (M14 §五/§十三).

Responsibilities
----------------
- ``rebuild()`` walks the Vault through ``VaultService.list_tree``/``read_bytes``
  (never ``open``/``pathlib``), chunks every Markdown note, stores chunks and
  embeds them in batches;
- ``handle_event()`` applies one watcher event (create/modify/delete/move) to the
  derived store, using content hashes so unchanged documents are skipped and a
  pure rename never re-embeds;
- ``status()`` reports the derived index state for the settings/status API.

Safety
------
Everything here writes **only** derived rows in ``.localnote/index.db``. The
Vault is read-only from this module's point of view; an embedding failure marks
one document ``pending``/``failed`` and never propagates into editing, FTS, or
startup.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from ..vault.errors import VaultError
from ..vault.events import VaultEvent
from ..vault.service import VaultService
from .chunking.markdown import MarkdownChunker
from .embeddings.base import EmbeddingError, EmbeddingProvider
from .embeddings.runner import EmbeddingRunner
from .schemas import RAGChunk, RAGIndexState, content_hash
from .vector.base import VectorStore

logger = logging.getLogger("localnote.rag.index")

_MARKDOWN_SUFFIXES = (".md", ".markdown")
EMBEDDING_VERSION = "v1"


@dataclass(slots=True)
class RagIndexResult:
    """Outcome of one rebuild or incremental pass."""

    indexed_documents: int = 0
    indexed_chunks: int = 0
    embedded_chunks: int = 0
    skipped_documents: int = 0
    failed_documents: int = 0
    deleted_documents: int = 0
    duration_ms: float = 0.0
    ready: bool = False
    embedding_degraded: bool = False
    degraded_reason: str | None = None


def _is_markdown(path: str) -> bool:
    return path.casefold().endswith(_MARKDOWN_SUFFIXES)


class RagIndexService:
    """Owns the RAG derived index for one Vault."""

    def __init__(
        self,
        vault: VaultService,
        store: VectorStore,
        *,
        chunker: MarkdownChunker | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        embedding_runner: EmbeddingRunner | None = None,
        embed_batch_size: int = 32,
        embedding_version: str = EMBEDDING_VERSION,
        enabled: bool = True,
        debounce_seconds: float = 1.5,
    ) -> None:
        self._vault = vault
        self._store = store
        self._chunker = chunker or MarkdownChunker()
        self._provider = embedding_provider
        self._runner = embedding_runner or (
            EmbeddingRunner(embedding_provider) if embedding_provider else None
        )
        self._batch_size = max(1, int(embed_batch_size))
        self._embedding_version = embedding_version
        self._enabled = bool(enabled)
        self._debounce = max(0.0, float(debounce_seconds))
        self._lock = threading.RLock()
        self._pending_paths: set[str] = set()
        self._debounce_timer: threading.Timer | None = None
        self._last_result = RagIndexResult()
        self._degraded_reason: str | None = None

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def store(self) -> VectorStore:
        return self._store

    @property
    def chunker(self) -> MarkdownChunker:
        return self._chunker

    @property
    def embedding_provider(self) -> EmbeddingProvider | None:
        return self._provider

    @property
    def embedding_model(self) -> str:
        return self._provider.model if self._provider is not None else ""

    @property
    def embedding_dimension(self) -> int:
        return int(self._provider.dimension) if self._provider is not None else 0

    @property
    def embedding_is_degraded(self) -> bool:
        return bool(getattr(self._provider, "is_degraded", False))

    @property
    def debounce_seconds(self) -> float:
        return self._debounce

    @property
    def pending_paths(self) -> list[str]:
        with self._lock:
            return sorted(self._pending_paths)

    def status(self) -> RAGIndexState:
        state = self._store.state()
        state.embedding_provider = type(self._provider).__name__ if self._provider else ""
        state.embedding_model = self.embedding_model
        state.embedding_version = self._embedding_version
        if self._provider is not None and self._provider.dimension:
            state.embedding_dimension = int(self._provider.dimension)
        return state

    def index_status(self) -> RAGIndexState:
        """Refresh counters from the tables, then overlay the live config."""
        with self._lock:
            state = self._store.refresh_counts()
        state.embedding_provider = (
            type(self._provider).__name__ if self._provider is not None else ""
        )
        state.embedding_model = self.embedding_model
        state.embedding_version = self._embedding_version
        if self._provider is not None and self._provider.dimension:
            state.embedding_dimension = int(self._provider.dimension)
        return state

    def embedding_consistent(self) -> bool:
        """True when stored vectors match the configured embedding settings."""
        state = self._store.state()
        if state.chunk_count == 0:
            return True
        if self._provider is None:
            return False
        return state.is_compatible_with(
            provider=type(self._provider).__name__,
            model=self.embedding_model,
            dimension=int(self._provider.dimension or state.embedding_dimension or 0),
            version=self._embedding_version,
        )

    def mark_embedding_outdated(self) -> None:
        """Flag stored vectors as stale (embedding config changed)."""
        state = self._store.state()
        state.status = "outdated"
        self._store.save_state(state)

    # ------------------------------------------------------------------
    # Full rebuild
    # ------------------------------------------------------------------

    def rebuild(self, *, progress: Callable[[int, int], None] | None = None) -> RagIndexResult:
        """Rebuild the whole derived RAG index from the Markdown files."""
        started = time.perf_counter()
        with self._lock:
            result = RagIndexResult()
            if not self._enabled:
                result.degraded_reason = "rag_disabled"
                result.duration_ms = (time.perf_counter() - started) * 1000.0
                return result
            try:
                self._store.ensure_ready()
            except Exception as exc:
                logger.warning("rag store unavailable during rebuild: %s", exc)
                result.degraded_reason = "index_unavailable"
                result.duration_ms = (time.perf_counter() - started) * 1000.0
                return result

            self._store.reset()
            paths = self._markdown_paths()
            total = len(paths)
            if progress is not None:
                progress(0, total)
            failures = 0
            for position, path in enumerate(paths, start=1):
                outcome = self._reindex_document(path, embed=True)
                if outcome == "indexed":
                    result.indexed_documents += 1
                elif outcome == "failed":
                    failures += 1
                    result.failed_documents += 1
                else:
                    result.skipped_documents += 1
                if progress is not None and (position % 25 == 0 or position == total):
                    progress(position, total)

            self._embed_pending(result)
            state = self._store.refresh_counts()
            state.embedding_provider = (
                type(self._provider).__name__ if self._provider is not None else ""
            )
            state.embedding_model = self.embedding_model
            state.embedding_version = self._embedding_version
            state.embedding_dimension = self.embedding_dimension
            state.last_indexed_at = datetime.now(UTC)
            self._store.save_state(state)

            result.indexed_chunks = state.chunk_count
            result.ready = state.status == "ready"
            result.embedding_degraded = self.embedding_is_degraded
            result.duration_ms = (time.perf_counter() - started) * 1000.0
            self._last_result = result
            self._degraded_reason = result.degraded_reason
            logger.info(
                "rag rebuild documents=%d chunks=%d embedded=%d failed=%d ms=%.1f",
                result.indexed_documents,
                state.chunk_count,
                result.embedded_chunks,
                result.failed_documents,
                result.duration_ms,
            )
            return result

    def _markdown_paths(self) -> list[str]:
        tree = self._vault.list_tree("", recursive=True)
        return sorted(
            (
                item.path
                for item in tree
                if item.kind == "file" and item.path != "." and _is_markdown(item.path)
            ),
            key=str.casefold,
        )

    # ------------------------------------------------------------------
    # Per-document work
    # ------------------------------------------------------------------

    def _reindex_document(self, path: str, *, embed: bool) -> str:
        """Index one document; returns ``indexed``/``skipped``/``failed``."""
        try:
            data, digest = self._vault.read_bytes(path)
        except VaultError:
            return "skipped"
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            self._store.mark_document_failed(path, "not_utf8")
            return "failed"

        try:
            chunks = self._chunker.chunk_document(path, text)
        except Exception:  # pragma: no cover - chunker is pure and defensive
            logger.exception("rag chunking failed path=%s", path)
            self._store.mark_document_failed(path, "chunking_failed")
            return "failed"

        if not chunks:
            self._store.delete_document(path)
            return "skipped"

        document_hash = content_hash(text)
        self._store.upsert_chunks(
            chunks, sha256=digest, content_hash=document_hash
        )
        if embed:
            self._embed_chunks(chunks)
        return "indexed"

    def _embed_chunks(
        self,
        chunks: Sequence[RAGChunk],
        result: RagIndexResult | None = None,
        *,
        reusable: dict[str, str] | None = None,
    ) -> int:
        """Embed and store vectors for ``chunks``; returns how many were stored.

        Chunks whose stored vector already matches ``(content_hash, model,
        embedding_version)`` are skipped, so editing one paragraph of a long
        note re-embeds one chunk rather than the whole note (M14 §五/§十三).
        """
        runner = self._runner
        if runner is None or not chunks:
            return 0
        wanted = list(chunks)
        existing = (
            reusable
            if reusable is not None
            else self._store.existing_embeddings(
                [chunk.chunk_id for chunk in wanted],
                model=self.embedding_model,
                embedding_version=self._embedding_version,
            )
        )
        pending = [
            chunk
            for chunk in wanted
            if existing.get(chunk.chunk_id) != chunk.content_hash
        ]
        if not pending:
            return 0

        stored = 0
        for start in range(0, len(pending), self._batch_size):
            window = pending[start : start + self._batch_size]
            texts = [chunk.embedding_text for chunk in window]
            try:
                vectors = runner.embed_documents(texts)
            except EmbeddingError as exc:
                logger.warning(
                    "rag embedding failed for %d chunks: %s", len(window), exc.code
                )
                self._store.mark_documents_pending(
                    sorted({chunk.document_id for chunk in window}), exc.code
                )
                if result is not None:
                    result.embedding_degraded = True
                    result.degraded_reason = exc.code
                continue
            except Exception as exc:  # pragma: no cover - provider bug surfaced
                logger.warning("rag embedding raised: %s", type(exc).__name__)
                self._store.mark_documents_pending(
                    sorted({chunk.document_id for chunk in window}), "embedding_error"
                )
                if result is not None:
                    result.embedding_degraded = True
                    result.degraded_reason = "embedding_error"
                continue
            if len(vectors) != len(window):
                logger.warning(
                    "rag embedding returned %d vectors for %d chunks",
                    len(vectors),
                    len(window),
                )
                continue
            self._store.upsert_embeddings(
                chunk_ids=[chunk.chunk_id for chunk in window],
                vectors=vectors,
                model=self.embedding_model,
                embedding_version=self._embedding_version,
            )
            stored += len(window)
        if result is not None:
            result.embedded_chunks += stored
        return stored

    def _embed_pending(self, result: RagIndexResult | None = None) -> int:
        """Embed every chunk whose vector is missing or stale (bounded batches)."""
        if self._runner is None:
            return 0
        total = 0
        while True:
            chunks = self._store.chunks_without_embeddings(
                limit=self._batch_size * 8,
                model=self.embedding_model,
                embedding_version=self._embedding_version,
            )
            if not chunks:
                break
            stored = self._embed_chunks(chunks, result)
            if stored == 0:
                break  # provider unavailable: stop instead of spinning
            total += stored
        return total

    def retry_failed(self) -> RagIndexResult:
        """Re-attempt every document whose embedding failed or is pending."""
        started = time.perf_counter()
        with self._lock:
            result = RagIndexResult()
            if not self._enabled:
                result.degraded_reason = "rag_disabled"
                return result
            result.embedded_chunks = self._embed_pending(result)
            state = self._store.refresh_counts()
            result.indexed_chunks = state.chunk_count
            result.indexed_documents = state.indexed_documents
            result.failed_documents = state.failed
            result.ready = state.status == "ready"
            result.embedding_degraded = self.embedding_is_degraded
            result.duration_ms = (time.perf_counter() - started) * 1000.0
            self._last_result = result
            return result

    # ------------------------------------------------------------------
    # Incremental sync (watcher)
    # ------------------------------------------------------------------

    def handle_event(self, event: VaultEvent) -> None:
        """Apply one watcher event, debounced and hash-guarded.

        Embedding is never called per keystroke: events are coalesced per path
        and flushed after ``debounce_seconds`` (default 1.5s).
        """
        if not self._enabled:
            return
        try:
            if event.kind == "move":
                self._apply_move(event.old_path or "", event.new_path or "")
                return
            path = event.path or ""
            if not _is_markdown(path):
                return
            with self._lock:
                self._pending_paths.add(path)
                if self._debounce_timer is not None:
                    self._debounce_timer.cancel()
                self._debounce_timer = threading.Timer(self._debounce, self.flush)
                self._debounce_timer.daemon = True
                self._debounce_timer.start()
        except Exception:  # pragma: no cover - watcher thread must never die
            logger.exception("rag event handling failed kind=%s", event.kind)

    def flush(self) -> RagIndexResult:
        """Process every buffered path now (also the synchronous test path)."""
        with self._lock:
            timer = self._debounce_timer
            self._debounce_timer = None
            if timer is not None:
                timer.cancel()
            paths = sorted(self._pending_paths)
            self._pending_paths.clear()
        result = RagIndexResult()
        if not paths:
            return result
        started = time.perf_counter()
        for path in paths:
            outcome = self._sync_path(path, result)
            if outcome == "indexed":
                result.indexed_documents += 1
            elif outcome == "deleted":
                result.deleted_documents += 1
            elif outcome == "failed":
                result.failed_documents += 1
            else:
                result.skipped_documents += 1
        state = self._store.refresh_counts()
        result.indexed_chunks = state.chunk_count
        result.ready = state.status == "ready"
        result.duration_ms = (time.perf_counter() - started) * 1000.0
        self._last_result = result
        return result

    def _sync_path(self, path: str, result: RagIndexResult) -> str:
        """Index/delete one path according to its current bytes."""
        try:
            data, digest = self._vault.read_bytes(path)
        except VaultError:
            if self._store.document_hash(path) is None:
                return "skipped"
            self._store.delete_document(path)
            return "deleted"
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            self._store.mark_document_failed(path, "not_utf8")
            return "failed"

        document_hash = content_hash(text)
        if self._store.document_content_hash(path) == document_hash:
            # Same document bytes: no re-chunk. Only a *missing* vector for one
            # of this document's chunks (an earlier embedding failure) is worth
            # another attempt, and that path never re-embeds unchanged chunks.
            stored = self._store.chunks_for_document(path)
            existing = self._store.existing_embeddings(
                [chunk.chunk_id for chunk in stored],
                model=self.embedding_model,
                embedding_version=self._embedding_version,
            )
            pending = [
                chunk
                for chunk in stored
                if existing.get(chunk.chunk_id) != chunk.content_hash
            ]
            if not pending:
                return "skipped"
            self._embed_chunks(pending, result, reusable={})
            return "indexed"
        try:
            chunks = self._chunker.chunk_document(path, text)
        except Exception:  # pragma: no cover
            logger.exception("rag chunking failed path=%s", path)
            self._store.mark_document_failed(path, "chunking_failed")
            return "failed"
        if not chunks:
            self._store.delete_document(path)
            return "deleted"
        # Collect reusable vector identities *before* replacing the chunk rows
        # (replacing a chunk cascades its embedding away), so an edit that only
        # changes one paragraph re-embeds one chunk instead of the whole note.
        reusable = self._store.existing_embeddings(
            [chunk.chunk_id for chunk in chunks],
            model=self.embedding_model,
            embedding_version=self._embedding_version,
        )
        self._store.upsert_chunks(chunks, sha256=digest, content_hash=document_hash)
        self._embed_chunks(chunks, result, reusable=reusable)
        return "indexed"

    def _apply_move(self, old_path: str, new_path: str) -> None:
        """Rename in the derived store; re-embed only when content changed."""
        if not old_path:
            return
        with self._lock:
            self._pending_paths.discard(old_path)
        if _is_markdown(old_path) and _is_markdown(new_path):
            stored_hash = self._store.document_content_hash(old_path)
            if stored_hash is not None:
                try:
                    data, digest = self._vault.read_bytes(new_path)
                except VaultError:
                    self._store.delete_document(old_path)
                    return
                try:
                    text = data.decode("utf-8")
                except UnicodeDecodeError:
                    self._store.delete_document(old_path)
                    self._store.mark_document_failed(new_path, "not_utf8")
                    return
                if content_hash(text) == stored_hash:
                    renamed = self._store.rename_document(old_path, new_path)
                    if renamed:
                        # Re-point the rows and refresh the Vault digest only:
                        # a pure rename must never re-chunk or re-embed.
                        self._store.set_document_sha256(new_path, digest)
                        return
        # Fall back to a full re-index of the new path (and drop the old rows).
        if old_path:
            self._store.delete_document(old_path)
        if new_path and _is_markdown(new_path):
            self._reindex_document(new_path, embed=True)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        with self._lock:
            if self._debounce_timer is not None:
                self._debounce_timer.cancel()
                self._debounce_timer = None
            self._pending_paths.clear()
        if self._runner is not None:
            self._runner.close()
        try:
            self._store.close()
        except Exception:  # pragma: no cover - defensive
            logger.exception("rag store close failed")

    @property
    def last_result(self) -> RagIndexResult:
        return self._last_result

    @property
    def degraded_reason(self) -> str | None:
        return self._degraded_reason


__all__ = ["EMBEDDING_VERSION", "RagIndexResult", "RagIndexService"]
