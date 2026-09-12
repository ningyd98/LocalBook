"""Performance checks for the RAG retrieval path (M14 §二十一).

Scale target: a personal Vault of ~1,000 Markdown notes / 5k–20k chunks, with
retrieval not depending on the chat model. These tests measure the *retrieval*
path (FTS + vector scan + RRF + context build) after indexing and print the
numbers; the thresholds are deliberately loose (environment variance) and their
purpose is to catch an order-of-magnitude regression, not to certify a laptop.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from server.rag.retrieval.hybrid import HybridRetriever
from server.rag.retrieval.keyword import KeywordRetriever
from server.rag.retrieval.vector import VectorRetriever
from tests.rag.conftest import write_note

NOTE_COUNT = 120
# Each note carries several full sections so the corpus reaches ~1–2 chunks per
# section; the point is to be in the same order of magnitude as the stated
# target (≈1,000 notes / 5–20k chunks) without a slow test.
CHUNKS_PER_NOTE = 6
RETRIEVAL_BUDGET_MS = 2000.0  # generous: a cold cache on a busy laptop still fits
CHUNK_BUDGET_MS = 4000.0


def _note(index: int) -> str:
    paragraphs = []
    for section in range(CHUNKS_PER_NOTE):
        body = (
            f"第 {index} 篇笔记的第 {section} 段内容，"
            "讨论云边协同、向量检索与本地模型部署的具体做法。"
            "快慢双系统要求边缘节点在时延超限时重新规划任务，重规划依据本地的任务图与算力估计；"
            "混合检索用 RRF 融合词法与向量排名，重排是可选项，引用必须来自检索证据。"
        )
        paragraphs.append(f"## 章节 {section}\n\n{body}\n")
    return f"# 笔记 {index}\n\n" + "\n".join(paragraphs)


@pytest.fixture(scope="module")
def large_vault(tmp_path_factory: pytest.TempPathFactory):
    from server.index.db import IndexDatabase
    from server.rag.chunking.markdown import MarkdownChunker
    from server.rag.embeddings.base import MockEmbeddingProvider
    from server.rag.embeddings.runner import EmbeddingRunner
    from server.rag.index_service import RagIndexService
    from server.rag.vector.sqlite import SqliteVectorStore
    from server.vault.service import VaultService

    root = Path(tmp_path_factory.mktemp("rag-perf")) / "vault"
    root.mkdir(parents=True)
    vault = VaultService(root, watcher_enabled=False)
    vault.initialize(start_watcher=False)
    for index in range(NOTE_COUNT):
        write_note(vault, f"notes/n{index:04d}.md", _note(index))

    provider = MockEmbeddingProvider(dimension=128)
    database = IndexDatabase(root / ".localnote" / "index.db")
    database.open()
    store = SqliteVectorStore(database)
    store.ensure_ready()
    index = RagIndexService(
        vault,
        store,
        chunker=MarkdownChunker(target_tokens=120, max_tokens=200, overlap_tokens=20),
        embedding_provider=provider,
        embedding_runner=EmbeddingRunner(provider),
        embed_batch_size=32,
    )
    started = time.perf_counter()
    result = index.rebuild()
    index_ms = (time.perf_counter() - started) * 1000.0
    print(
        f"\n[perf] indexed {result.indexed_documents} notes / "
        f"{store.chunk_count()} chunks in {index_ms:.0f} ms"
    )
    try:
        yield {
            "vault": vault,
            "store": store,
            "index": index,
            "provider": provider,
            "index_ms": index_ms,
        }
    finally:
        index.close()
        database.close()


def test_index_scale_is_as_expected(large_vault) -> None:
    store = large_vault["store"]
    assert store.document_count() == NOTE_COUNT
    assert store.chunk_count() >= NOTE_COUNT * CHUNKS_PER_NOTE
    assert store.embedded_count() == store.chunk_count()


def test_retrieval_latency_stays_within_budget(large_vault) -> None:
    store = large_vault["store"]
    provider = large_vault["provider"]
    hybrid = HybridRetriever(
        keyword=KeywordRetriever(store),
        vector=VectorRetriever(store, provider=provider),
    )
    # Warm the caches, then measure the steady-state path (no chat model).
    hybrid.search("云边协同 向量检索")
    samples = []
    for _ in range(5):
        started = time.perf_counter()
        outcome = hybrid.search("云边协同 向量检索 本地模型")
        samples.append((time.perf_counter() - started) * 1000.0)
        assert outcome.results
    worst = max(samples)
    median = sorted(samples)[len(samples) // 2]
    print(
        f"[perf] hybrid retrieval over {store.chunk_count()} chunks: "
        f"median {median:.1f} ms / worst {worst:.1f} ms "
        f"(fts={outcome.stats.fts_candidates}, vector={outcome.stats.vector_candidates})"
    )
    assert worst < RETRIEVAL_BUDGET_MS, f"retrieval took {worst:.1f} ms"


def test_chunking_latency_is_bounded(large_vault) -> None:
    from server.rag.chunking.markdown import MarkdownChunker

    chunker = MarkdownChunker(target_tokens=120, max_tokens=200, overlap_tokens=20)
    body = _note(1)
    iterations = 20
    started = time.perf_counter()
    for _ in range(iterations):
        chunker.chunk_document("notes/n0001.md", body)
    per_note_ms = (time.perf_counter() - started) * 1000.0 / iterations
    print(f"[perf] chunking one note: {per_note_ms:.2f} ms")
    assert per_note_ms < CHUNK_BUDGET_MS


def test_incremental_modify_does_not_reembed_the_whole_vault(large_vault) -> None:
    store = large_vault["store"]
    vault = large_vault["vault"]
    provider = large_vault["provider"]
    index = large_vault["index"]

    before_calls = sum(len(batch) for batch in provider.document_calls)
    before_embedded = store.embedded_count()
    path = "notes/n0007.md"
    write_note(vault, path, _note(7) + "\n## 新增小节\n\n只改动了这一段。\n")
    index.handle_event(_event(path, "modify"))
    result = index.flush()
    delta_calls = sum(len(batch) for batch in provider.document_calls) - before_calls
    print(
        f"[perf] one-note edit embedded {delta_calls} chunk(s) "
        f"(vault has {store.chunk_count()} chunks)"
    )
    assert result.indexed_documents == 1
    assert delta_calls <= CHUNKS_PER_NOTE + 2
    assert store.embedded_count() == store.chunk_count()
    assert store.embedded_count() >= before_embedded


def test_unchanged_flush_costs_no_embedding(large_vault) -> None:
    store = large_vault["store"]
    vault = large_vault["vault"]
    provider = large_vault["provider"]
    index = large_vault["index"]
    before = sum(len(batch) for batch in provider.document_calls)
    path = "notes/n0008.md"
    _ = vault.read_bytes(path)
    index.handle_event(_event(path, "modify"))
    result = index.flush()
    assert result.embedded_chunks == 0
    assert sum(len(batch) for batch in provider.document_calls) == before
    assert store.embedded_count() == store.chunk_count()


def _event(path: str, kind: str):
    from server.vault.events import VaultEvent

    return VaultEvent(kind=kind, path=path)
