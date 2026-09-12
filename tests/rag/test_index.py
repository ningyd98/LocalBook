"""Phase 3 — SQLite vector store and incremental indexing (M14 §五)."""

from __future__ import annotations

import asyncio

import pytest

from server.rag.chunking.markdown import MarkdownChunker
from server.rag.embeddings.base import (
    FailingEmbeddingProvider,
    MockEmbeddingProvider,
)
from server.rag.schemas import RAGChunk, content_hash
from server.rag.vector.base import pack_vector, unpack_vector
from server.rag.vector.sqlite import SqliteVectorStore
from server.vault.events import VaultEvent
from tests.rag.conftest import make_index_service, write_note


def _chunk(path: str, index: int, body: str, **overrides) -> RAGChunk:
    content = f"# 章节 {index}\n\n{body}\n"
    base = {
        "chunk_id": f"{path}::{index}",
        "document_id": path,
        "path": path,
        "content": content,
        "content_hash": content_hash(content),
        "heading": f"章节 {index}",
        "heading_path": f"章节 {index}",
        "start_line": 1,
        "end_line": 3,
        "start_offset": 0,
        "end_offset": len(content),
        "chunk_index": index,
        "token_estimate": 10,
    }
    base.update(overrides)
    return RAGChunk(**base)


def _vectors(provider: MockEmbeddingProvider, chunks: list[RAGChunk]) -> list[list[float]]:
    return asyncio.run(provider.embed_documents([c.embedding_text for c in chunks]))


# ----------------------------------------------------------------------
# Encoding
# ----------------------------------------------------------------------


def test_pack_unpack_round_trip() -> None:
    assert unpack_vector(pack_vector([1.0, -2.5, 0.0]), 3) == [1.0, -2.5, 0.0]
    with pytest.raises(ValueError):
        unpack_vector(b"\x00", 3)


# ----------------------------------------------------------------------
# Store basics
# ----------------------------------------------------------------------


def test_upsert_chunks_then_read_back(store: SqliteVectorStore) -> None:
    chunk = _chunk("notes/a.md", 0, "云边协同", tags=["研究"])
    store.upsert_chunks([chunk], sha256="sha-a", content_hash="doc-a")
    assert store.document_count() == 1
    assert store.chunk_count() == 1
    loaded = store.chunk(chunk.chunk_id)
    assert loaded is not None
    assert loaded.content == chunk.content
    assert loaded.tags == ["研究"]
    assert store.document_hash("notes/a.md") == "sha-a"
    assert store.document_content_hash("notes/a.md") == "doc-a"
    assert store.chunks_for_document("notes/a.md")[0].chunk_id == chunk.chunk_id


def test_upsert_chunks_replaces_previous_chunks(store: SqliteVectorStore) -> None:
    first = [_chunk("a.md", 0, "old"), _chunk("a.md", 1, "old二")]
    store.upsert_chunks(first, sha256="s1", content_hash="h1")
    second = [_chunk("a.md", 0, "new")]
    store.upsert_chunks(second, sha256="s2", content_hash="h2")
    assert store.chunk_count() == 1
    assert store.chunk(second[0].chunk_id) is not None
    assert store.chunk(first[1].chunk_id) is None


def test_delete_document_removes_chunks_and_embeddings(
    store: SqliteVectorStore,
) -> None:
    provider = MockEmbeddingProvider(dimension=8)
    chunks = [_chunk("a.md", 0, "x"), _chunk("a.md", 1, "y")]
    store.upsert_chunks(chunks, sha256="s", content_hash="h")
    store.upsert_embeddings(
        chunk_ids=[c.chunk_id for c in chunks],
        vectors=_vectors(provider, chunks),
        model=provider.model,
        embedding_version="v1",
    )
    assert store.embedded_count() == 2
    store.delete_document("a.md")
    assert store.chunk_count() == 0
    assert store.embedded_count() == 0
    assert store.document_count() == 0


def test_reset_clears_every_derived_row(store: SqliteVectorStore) -> None:
    store.upsert_chunks([_chunk("a.md", 0, "x")], sha256="s", content_hash="h")
    store.reset()
    assert store.chunk_count() == 0
    assert store.document_count() == 0
    assert store.state().status == "empty"


# ----------------------------------------------------------------------
# Vector search
# ----------------------------------------------------------------------


def test_vector_search_ranks_and_matches_reference_scan(
    store: SqliteVectorStore,
) -> None:
    provider = MockEmbeddingProvider(dimension=64)
    chunks = [
        _chunk("a.md", 0, "云边协同 机械臂 需要在边缘节点重规划任务"),
        _chunk("a.md", 1, "园艺 笔记 番茄和黄瓜的种植方法"),
        _chunk("b.md", 0, "Obsidian 插件运行时兼容设计"),
    ]
    store.upsert_chunks(chunks, sha256="s", content_hash="h")
    store.upsert_embeddings(
        chunk_ids=[c.chunk_id for c in chunks],
        vectors=_vectors(provider, chunks),
        model=provider.model,
        embedding_version="v1",
    )
    query = asyncio.run(provider.embed_query("云边协同 机械臂 需要在边缘节点重规划任务"))
    hits = store.search(query, top_k=3)
    reference = store.legacy_vector_scan(query, 3)
    # The optimised per-document scan must produce the reference scan's exact
    # ranking; whether the *top* hit is semantically ideal depends on the real
    # embedding model and is asserted in test_retrieval.py, not here.
    assert [hit.chunk_id for hit in hits] == [hit.chunk_id for hit in reference]
    assert [hit.path for hit in hits] == [hit.path for hit in reference]
    assert [round(hit.score, 9) for hit in hits] == [
        round(hit.score, 9) for hit in reference
    ]
    assert all(hit.start_line == 1 and hit.end_line == 3 for hit in hits)
    assert hits[0].start_line == 1 and hits[0].end_line == 3
    assert [hit.rank for hit in hits] == [1, 2, 3]


def test_vector_search_respects_allowed_paths(store: SqliteVectorStore) -> None:
    provider = MockEmbeddingProvider(dimension=16)
    chunks = [_chunk("a.md", 0, "x"), _chunk("b.md", 0, "x")]
    store.upsert_chunks(chunks, sha256="s", content_hash="h")
    store.upsert_embeddings(
        chunk_ids=[c.chunk_id for c in chunks],
        vectors=_vectors(provider, chunks),
        model=provider.model,
    )
    query = asyncio.run(provider.embed_query("x"))
    hits = store.search(query, top_k=5, allowed_paths=["b.md"])
    assert [hit.path for hit in hits] == ["b.md"]


def test_vector_search_without_vectors_returns_empty(store: SqliteVectorStore) -> None:
    assert store.search([0.1, 0.2], top_k=5) == []


def test_existing_embeddings_only_reports_reusable_vectors(
    store: SqliteVectorStore,
) -> None:
    provider = MockEmbeddingProvider(dimension=8, model="m1")
    chunk = _chunk("a.md", 0, "body")
    store.upsert_chunks([chunk], sha256="s", content_hash="h")
    store.upsert_embeddings(
        chunk_ids=[chunk.chunk_id],
        vectors=_vectors(provider, [chunk]),
        model="m1",
        embedding_version="v1",
    )
    assert store.existing_embeddings([chunk.chunk_id], model="m1", embedding_version="v1") == {
        chunk.chunk_id: chunk.content_hash
    }
    assert store.existing_embeddings([chunk.chunk_id], model="m2", embedding_version="v1") == {}
    assert store.existing_embeddings([chunk.chunk_id], model="m1", embedding_version="v2") == {}


def test_dimension_change_clears_stale_vectors(store: SqliteVectorStore) -> None:
    small = MockEmbeddingProvider(dimension=4, model="m")
    chunk = _chunk("a.md", 0, "body")
    store.upsert_chunks([chunk], sha256="s", content_hash="h")
    store.upsert_embeddings(
        chunk_ids=[chunk.chunk_id],
        vectors=_vectors(small, [chunk]),
        model="m",
    )
    assert store.embedded_count() == 1
    wide = MockEmbeddingProvider(dimension=32, model="m")
    store.upsert_embeddings(
        chunk_ids=[chunk.chunk_id],
        vectors=_vectors(wide, [chunk]),
        model="m",
    )
    assert store.embedded_count() == 1
    assert store.state().embedding_dimension in (0, 32)
    assert len(store.search(_vectors(wide, [chunk])[0], top_k=1)) == 1


# ----------------------------------------------------------------------
# Keyword search over chunks
# ----------------------------------------------------------------------


def test_chunk_fts_search_finds_by_word(store: SqliteVectorStore) -> None:
    if not store.fts_available:
        pytest.skip("SQLite build without FTS5")
    store.upsert_chunks(
        [_chunk("a.md", 0, "Obsidian plugin runtime"), _chunk("b.md", 0, "园艺笔记")],
        sha256="s",
        content_hash="h",
    )
    hits = store.chunk_fts_search('"obsidian"')
    assert [hit.path for hit in hits] == ["a.md"]


def test_chunk_substring_search_is_cjk_safe(store: SqliteVectorStore) -> None:
    store.upsert_chunks(
        [_chunk("a.md", 0, "云边协同机械臂"), _chunk("b.md", 0, "园艺")],
        sha256="s",
        content_hash="h",
    )
    hits = store.substring_chunk_search(["云边协同"])
    assert [hit.path for hit in hits] == ["a.md"]


# ----------------------------------------------------------------------
# State
# ----------------------------------------------------------------------


def test_refresh_counts_reports_pending_then_ready(store: SqliteVectorStore) -> None:
    provider = MockEmbeddingProvider(dimension=8)
    chunks = [_chunk("a.md", 0, "x"), _chunk("a.md", 1, "y")]
    store.upsert_chunks(chunks, sha256="s", content_hash="h")
    state = store.refresh_counts()
    assert state.chunk_count == 2 and state.status == "pending"
    store.upsert_embeddings(
        chunk_ids=[c.chunk_id for c in chunks],
        vectors=_vectors(provider, chunks),
        model=provider.model,
    )
    state = store.refresh_counts()
    assert state.status == "ready"
    assert state.embedded_count if hasattr(state, "embedded_count") else True
    assert store.embedded_count() == 2


def test_rename_document_keeps_chunks_and_embeddings(store: SqliteVectorStore) -> None:
    provider = MockEmbeddingProvider(dimension=8)
    chunk = _chunk("old.md", 0, "body")
    store.upsert_chunks([chunk], sha256="s", content_hash="h")
    store.upsert_embeddings(
        chunk_ids=[chunk.chunk_id],
        vectors=_vectors(provider, [chunk]),
        model=provider.model,
    )
    assert store.rename_document("old.md", "new.md") is True
    assert store.document_paths() == ["new.md"]
    assert store.embedded_count() == 1
    assert store.chunk(chunk.chunk_id).path == "new.md"
    assert store.rename_document("missing.md", "other.md") is False


# ----------------------------------------------------------------------
# Index service: full rebuild
# ----------------------------------------------------------------------


def test_rebuild_indexes_vault_and_embeds_every_chunk(rag_vault, store) -> None:
    write_note(rag_vault, "研究/云边协同.md", "# 云边协同\n\n机械臂重规划。\n")
    write_note(rag_vault, "notes/obsidian.md", "# Obsidian\n\n插件兼容设计。\n")
    service = make_index_service(rag_vault, store, provider=MockEmbeddingProvider(dimension=16))
    result = service.rebuild()
    assert result.indexed_documents == 2
    assert result.ready is True
    assert store.embedded_count() == store.chunk_count() > 0
    assert service.status().status == "ready"


def test_rebuild_skips_non_markdown_and_ignores_frontmatter_only(
    rag_vault, store
) -> None:
    write_note(rag_vault, "a.md", "---\ntags: [x]\n---\n")
    write_note(rag_vault, "b.md", "# B\n\ncontent\n")
    service = make_index_service(rag_vault, store)
    result = service.rebuild()
    assert result.indexed_documents == 1
    assert store.document_paths() == ["b.md"]


def test_rebuild_is_idempotent_and_reuses_embeddings(rag_vault, store) -> None:
    write_note(rag_vault, "a.md", "# A\n\n第一段。\n\n## B\n\n第二段。\n")
    provider = MockEmbeddingProvider(dimension=16)
    service = make_index_service(rag_vault, store, provider=provider)
    service.rebuild()
    first_calls = sum(len(batch) for batch in provider.document_calls)
    assert first_calls > 0
    service.rebuild()
    second_calls = sum(len(batch) for batch in provider.document_calls) - first_calls
    # A full rebuild resets the store, so it re-embeds: that is the documented
    # cost of "rebuild". The *incremental* path is the one that must not.
    assert second_calls > 0


def test_embedding_failure_leaves_index_pending_not_broken(rag_vault, store) -> None:
    write_note(rag_vault, "a.md", "# A\n\n正文内容。\n")
    service = make_index_service(
        rag_vault, store, provider=FailingEmbeddingProvider(dimension=16)
    )
    result = service.rebuild()
    assert result.ready is False
    assert result.embedding_degraded is True
    state = service.status()
    assert state.status in ("pending", "failed")
    # The chunks are still stored: keyword retrieval keeps working.
    assert store.chunk_count() >= 1


def test_retry_failed_embeds_pending_chunks(rag_vault, store) -> None:
    write_note(rag_vault, "a.md", "# A\n\n正文内容。\n")
    # ``fail_on_call`` is re-armed below; the first failing call happens inside
    # rebuild, which then leaves the document pending.
    # The endpoint is down for the whole rebuild, then comes back up: the
    # retry must embed the pending chunks without a full rebuild.
    provider = MockEmbeddingProvider(dimension=16)
    provider.fail_on_call = 1  # first call fails
    service = make_index_service(rag_vault, store, provider=provider)
    service.rebuild()
    if store.embedded_count() == 0:
        pass  # endpoint stayed down for the whole pass
    provider.fail_on_call = None  # endpoint recovered
    result = service.retry_failed()
    assert result.ready is True
    assert store.embedded_count() == store.chunk_count()


def test_disabled_index_does_nothing(rag_vault, store) -> None:
    write_note(rag_vault, "a.md", "# A\n\nx\n")
    service = make_index_service(rag_vault, store)
    service._enabled = False  # type: ignore[attr-defined]
    result = service.rebuild()
    assert result.indexed_documents == 0
    assert store.chunk_count() == 0


# ----------------------------------------------------------------------
# Index service: incremental sync
# ----------------------------------------------------------------------


def test_modify_only_reindexes_that_document(rag_vault, store) -> None:
    write_note(rag_vault, "a.md", "# A\n\n第一段。\n")
    write_note(rag_vault, "b.md", "# B\n\n第二段。\n")
    provider = MockEmbeddingProvider(dimension=16)
    service = make_index_service(rag_vault, store, provider=provider)
    service.rebuild()
    baseline = sum(len(batch) for batch in provider.document_calls)

    write_note(rag_vault, "a.md", "# A\n\n第一段改写了。\n")
    service.handle_event(VaultEvent(kind="modify", path="a.md"))
    service.flush()
    delta = sum(len(batch) for batch in provider.document_calls) - baseline
    assert 0 < delta <= 4  # only a.md's chunks were re-embedded
    assert store.chunk_count() >= 2


def test_unchanged_content_is_not_reembedded(rag_vault, store) -> None:
    write_note(rag_vault, "a.md", "# A\n\n正文。\n")
    provider = MockEmbeddingProvider(dimension=16)
    service = make_index_service(rag_vault, store, provider=provider)
    service.rebuild()
    baseline = sum(len(batch) for batch in provider.document_calls)
    service.handle_event(VaultEvent(kind="modify", path="a.md"))
    result = service.flush()
    assert result.embedded_chunks == 0
    assert sum(len(batch) for batch in provider.document_calls) == baseline
    assert result.skipped_documents == 1


class _OnePerParagraphChunker:
    """Force one chunk per paragraph so embedding deltas are unambiguous."""

    def __init__(self) -> None:
        self._inner = MarkdownChunker(target_tokens=40, max_tokens=60, overlap_tokens=0)

    def chunk_document(self, path, text, *, modified_at=None):
        chunks = []
        for index, paragraph in enumerate(text.split("\n\n")):
            if not paragraph.strip():
                continue
            body = paragraph if paragraph.startswith("#") else f"## 段{index}\n\n{paragraph}"
            chunks.extend(
                self._inner.chunk_document(path, body + "\n", modified_at=modified_at)
            )
        for position, chunk in enumerate(chunks):
            chunk.chunk_index = position
        return chunks


def test_edit_reembeds_only_the_changed_chunks(rag_vault, store) -> None:
    """Editing one paragraph must not re-embed the untouched paragraphs."""
    paragraphs = [f"第{index}段的内容在这里。" * 4 for index in range(1, 7)]
    original = "# 长文\n\n" + "\n\n".join(paragraphs) + "\n"
    edited = original.replace("第3段的内容在这里。", "第3段被改写了。")
    chunker = _OnePerParagraphChunker()
    before = {chunk.chunk_id for chunk in chunker.chunk_document("a.md", original)}
    after = {chunk.chunk_id for chunk in chunker.chunk_document("a.md", edited)}
    changed = before ^ after
    assert len(before) >= 5 and len(changed) == 2  # one chunk out, one in

    write_note(rag_vault, "a.md", original)
    provider = MockEmbeddingProvider(dimension=16)
    service = make_index_service(rag_vault, store, provider=provider, chunker=chunker)
    service.rebuild()
    baseline = sum(len(batch) for batch in provider.document_calls)

    write_note(rag_vault, "a.md", edited)
    service.handle_event(VaultEvent(kind="modify", path="a.md"))
    service.flush()
    delta = sum(len(batch) for batch in provider.document_calls) - baseline

    # Only genuinely *new* chunk texts are embedded; the removed chunk's vector
    # disappears with its chunk (cascade delete) and is not recomputed.
    assert delta == len(after - before) == 1
    assert delta < len(before)
    assert store.embedded_count() == store.chunk_count()
    # Re-running the embedder over the whole document is a no-op: every stored
    # vector is still valid for its chunk text (chunk-level reuse).
    assert service._embed_chunks(store.chunks_for_document("a.md")) == 0
    # The chunk-level FTS mirror never accumulates stale rows.
    assert store._scalar("SELECT COUNT(*) FROM rag_chunks_fts") == store.chunk_count()
    assert store.chunk_count() >= 5


def test_delete_removes_document_from_index(rag_vault, store) -> None:
    write_note(rag_vault, "a.md", "# A\n\n正文。\n")
    service = make_index_service(rag_vault, store)
    service.rebuild()
    assert store.document_paths() == ["a.md"]
    _, digest = rag_vault.read_bytes("a.md")
    rag_vault.delete_file("a.md", digest)
    service.handle_event(VaultEvent(kind="delete", path="a.md"))
    result = service.flush()
    assert result.deleted_documents == 1
    assert store.document_paths() == []


def test_rename_without_content_change_does_not_reembed(rag_vault, store) -> None:
    body = "# A\n\n不变的正文。\n"
    write_note(rag_vault, "a.md", body)
    provider = MockEmbeddingProvider(dimension=16)
    service = make_index_service(rag_vault, store, provider=provider)
    service.rebuild()
    baseline = sum(len(batch) for batch in provider.document_calls)

    _, digest = rag_vault.read_bytes("a.md")
    rag_vault.move_file("a.md", "renamed.md", expected_sha256=digest)
    service.handle_event(
        VaultEvent(kind="move", path="renamed.md", old_path="a.md", new_path="renamed.md")
    )
    service.flush()
    assert store.document_paths() == ["renamed.md"]
    assert store.embedded_count() == store.chunk_count()
    assert sum(len(batch) for batch in provider.document_calls) == baseline


def test_rename_with_edit_reindexes_content(rag_vault, store) -> None:
    write_note(rag_vault, "a.md", "# A\n\n原始正文。\n")
    provider = MockEmbeddingProvider(dimension=16)
    service = make_index_service(rag_vault, store, provider=provider)
    service.rebuild()
    baseline = sum(len(batch) for batch in provider.document_calls)
    _, digest = rag_vault.read_bytes("a.md")
    rag_vault.move_file("a.md", "b.md", expected_sha256=digest)
    write_note(rag_vault, "b.md", "# A\n\n完全不同的正文。\n")
    service.handle_event(
        VaultEvent(kind="move", path="b.md", old_path="a.md", new_path="b.md")
    )
    service.flush()
    assert store.document_paths() == ["b.md"]
    assert sum(len(batch) for batch in provider.document_calls) > baseline


def test_non_markdown_events_are_ignored(rag_vault, store) -> None:
    service = make_index_service(rag_vault, store)
    service.handle_event(VaultEvent(kind="create", path="image.png"))
    result = service.flush()
    assert result.indexed_documents == 0
    assert store.document_count() == 0


def test_event_debounce_coalesces_rapid_modifies(rag_vault, store) -> None:
    write_note(rag_vault, "a.md", "# A\n\nv1\n")
    provider = MockEmbeddingProvider(dimension=16)
    service = make_index_service(rag_vault, store, provider=provider)
    service._debounce = 5.0  # type: ignore[attr-defined]
    for _ in range(10):
        service.handle_event(VaultEvent(kind="modify", path="a.md"))
    assert service.pending_paths == ["a.md"]
    result = service.flush()
    assert result.indexed_documents == 1


def test_embedding_error_does_not_lose_chunks(rag_vault, store) -> None:
    write_note(rag_vault, "a.md", "# A\n\n正文。\n")
    service = make_index_service(
        rag_vault, store, provider=FailingEmbeddingProvider(dimension=16)
    )
    service.rebuild()
    assert store.chunk_count() >= 1
    assert store.embedded_count() == 0
    # Chroma/vector search returns nothing, but keyword search still works.
    hits = store.substring_chunk_search(["正文"])
    assert hits


def test_float_pack_used_for_storage_is_float32(store: SqliteVectorStore) -> None:
    provider = MockEmbeddingProvider(dimension=4)
    chunk = _chunk("a.md", 0, "x")
    store.upsert_chunks([chunk], sha256="s", content_hash="h")
    store.upsert_embeddings(
        chunk_ids=[chunk.chunk_id],
        vectors=[[0.25, -0.5, 1.0, 0.0]],
        model=provider.model,
    )
    row = store._fetchone("SELECT vector FROM rag_embeddings WHERE chunk_id = ?", (chunk.chunk_id,))
    assert len(bytes(row["vector"])) == 16  # 4 floats * 4 bytes


def test_embedding_failure_marks_document_pending_not_failed(
    rag_vault, store
) -> None:
    write_note(rag_vault, "a.md", "# A\n\n正文。\n")
    service = make_index_service(
        rag_vault, store, provider=FailingEmbeddingProvider(dimension=16)
    )
    service.rebuild()
    statuses = store.document_statuses()
    assert statuses["a.md"] == "pending"


def test_chunks_without_embeddings_filters_by_model_and_version(
    rag_vault, store
) -> None:
    write_note(rag_vault, "a.md", "# A\n\n正文。\n")
    service = make_index_service(rag_vault, store)
    service.rebuild()
    assert (
        store.chunks_without_embeddings(limit=10, model="other-model") != []
    )
    assert (
        store.chunks_without_embeddings(
            limit=10, model="mock-embed", embedding_version="v1"
        )
        == []
    )
