"""Phase 4/5 — retrieval: keyword, vector, RRF fusion and hybrid (M14 §六)."""

from __future__ import annotations

import asyncio

import pytest

from server.rag.embeddings.base import FailingEmbeddingProvider, MockEmbeddingProvider
from server.rag.rerank.base import LexicalOverlapReranker
from server.rag.retrieval.base import Retriever, RetrieverUnavailable
from server.rag.retrieval.hybrid import HybridRetriever
from server.rag.retrieval.keyword import (
    KeywordRetriever,
    fts_match_expression,
    tokenize,
    validate_query,
)
from server.rag.retrieval.rrf import fuse_scores, reciprocal_rank_fusion
from server.rag.retrieval.vector import VectorRetriever
from server.rag.schemas import RetrievalResult
from tests.rag.conftest import make_index_service, write_note


def _with_keyword_rank(item: RetrievalResult, rank: int) -> RetrievalResult:
    item.keyword_rank = rank
    return item


def _with_vector_rank(item: RetrievalResult, rank: int) -> RetrievalResult:
    item.vector_rank = rank
    return item


def _result(chunk_id: str, path: str = "a.md", content: str = "text") -> RetrievalResult:
    return RetrievalResult(
        chunk_id=chunk_id,
        path=path,
        heading="H",
        heading_path="H",
        content=content,
    )


# ----------------------------------------------------------------------
# Query handling
# ----------------------------------------------------------------------


def test_tokenize_and_validate() -> None:
    assert tokenize("云边 协同 云边") == ["云边", "协同"]
    with pytest.raises(ValueError):
        validate_query("   ")
    with pytest.raises(ValueError):
        validate_query("x" * 300)
    with pytest.raises(ValueError):
        validate_query("bad\x00term")


def test_fts_match_expression_only_for_ascii_terms() -> None:
    assert fts_match_expression(["obsidian", "plugin"]) == '"obsidian" "plugin"'
    assert fts_match_expression(["云边协同"]) is None
    assert fts_match_expression([]) is None


# ----------------------------------------------------------------------
# RRF
# ----------------------------------------------------------------------


def test_rrf_prefers_chunks_found_by_both_retrievers() -> None:
    keyword = [_with_keyword_rank(_result("k1"), 1), _with_keyword_rank(_result("both"), 2)]
    vector = [_with_vector_rank(_result("both"), 1), _with_vector_rank(_result("v2"), 2)]
    fused = reciprocal_rank_fusion([keyword, vector], k=60)
    assert fused[0].chunk_id == "both"
    assert fused[0].score > fused[1].score
    assert {item.chunk_id for item in fused} == {"k1", "both", "v2"}
    assert fused[0].keyword_rank == 2 and fused[0].vector_rank == 1


def test_rrf_is_deterministic_and_respects_top_k() -> None:
    first = reciprocal_rank_fusion([[_result("b"), _result("a")]], k=60)
    second = reciprocal_rank_fusion([[_result("b"), _result("a")]], k=60)
    assert [item.chunk_id for item in first] == [item.chunk_id for item in second]
    tied = reciprocal_rank_fusion([[_result("z")], [_result("a")]], k=60)
    assert [item.chunk_id for item in tied] == ["a", "z"]  # deterministic tiebreak
    assert len(reciprocal_rank_fusion([[_result("a"), _result("b")]], top_k=1)) == 1


def test_rrf_uses_one_over_k_plus_rank() -> None:
    scores = fuse_scores([("a", 1), ("a", 3), ("b", 1)], k=10)
    assert scores["a"] == pytest.approx(1 / 11 + 1 / 13)
    assert scores["b"] == pytest.approx(1 / 11)


def test_rrf_rejects_bad_k() -> None:
    with pytest.raises(ValueError):
        reciprocal_rank_fusion([[_result("a")]], k=0)


def test_rrf_consumes_the_link_rank() -> None:
    """The link/graph list is fused by its own rank, exactly like the others."""
    item = _result("l1")
    item.link_rank = 7
    fused = reciprocal_rank_fusion([[item], [_with_keyword_rank(_result("k1"), 1)]], k=60)
    assert fused[0].chunk_id == "k1"  # rank 1 beats rank 7
    assert fused[0].score == pytest.approx(1 / 61)
    assert fused[1].score == pytest.approx(1 / 67)
    assert fused[1].link_rank == 7


def test_rrf_weights_scale_each_list() -> None:
    """A list's contribution can be down-weighted (indirect evidence)."""
    keyword = [_with_keyword_rank(_result("k1"), 1)]
    link = [_result("l1")]
    link[0].link_rank = 1
    plain = reciprocal_rank_fusion([keyword, link], k=60)
    assert plain[0].chunk_id == "k1" and plain[0].score == plain[1].score
    weighted = reciprocal_rank_fusion([keyword, link], k=60, weights=[1.0, 0.5])
    assert weighted[0].chunk_id == "k1"
    assert weighted[1].score == pytest.approx(0.5 / 61)
    with pytest.raises(ValueError):
        reciprocal_rank_fusion([keyword, link], k=60, weights=[1.0])


# ----------------------------------------------------------------------
# Keyword retriever
# ----------------------------------------------------------------------


def test_keyword_retriever_uses_chunk_fts_for_english(rag_vault, store) -> None:
    write_note(rag_vault, "a.md", "# Obsidian\n\nPlugin runtime compatibility.\n")
    write_note(rag_vault, "b.md", "# 园艺\n\n番茄种植笔记。\n")
    make_index_service(rag_vault, store).rebuild()
    retriever = KeywordRetriever(store)
    hits = retriever.retrieve("obsidian plugin", top_k=5)
    assert [hit.path for hit in hits] == ["a.md"]
    assert hits[0].keyword_rank == 1
    assert hits[0].start_line >= 1


def test_keyword_retriever_falls_back_to_substring_for_chinese(
    rag_vault, store
) -> None:
    write_note(rag_vault, "a.md", "# 云边协同\n\n机械臂需要在边缘节点重规划任务。\n")
    make_index_service(rag_vault, store).rebuild()
    retriever = KeywordRetriever(store)
    hits = retriever.retrieve("云边协同", top_k=5)
    assert [hit.path for hit in hits] == ["a.md"]
    assert "云边协同" in hits[0].content


def test_keyword_retriever_implements_protocol(rag_vault, store) -> None:
    assert isinstance(KeywordRetriever(store), Retriever)


def test_keyword_retriever_respects_allowed_paths(rag_vault, store) -> None:
    write_note(rag_vault, "a.md", "# A\n\n云边协同\n")
    write_note(rag_vault, "b.md", "# B\n\n云边协同\n")
    make_index_service(rag_vault, store).rebuild()
    retriever = KeywordRetriever(store)
    hits = retriever.retrieve("云边协同", top_k=5, allowed_paths=["b.md"])
    assert [hit.path for hit in hits] == ["b.md"]


def test_keyword_retriever_without_index_returns_empty(rag_vault, store) -> None:
    retriever = KeywordRetriever(store)
    assert retriever.retrieve("nothing here", top_k=5) == []


# ----------------------------------------------------------------------
# Vector retriever
# ----------------------------------------------------------------------


def test_vector_retriever_requires_a_provider(rag_vault, store) -> None:
    retriever = VectorRetriever(store)
    assert retriever.available is False
    with pytest.raises(RetrieverUnavailable):
        retriever.retrieve("query", top_k=5)


def test_vector_retriever_reports_provider_failure(rag_vault, store) -> None:
    provider = FailingEmbeddingProvider(dimension=8)
    retriever = VectorRetriever(store, provider=provider)
    with pytest.raises(RetrieverUnavailable):
        retriever.retrieve("query", top_k=5)


def test_vector_retriever_finds_a_semantic_match_fts_misses(rag_vault, store) -> None:
    """A query with no shared keywords still finds the right note by vector."""
    write_note(
        rag_vault,
        "a.md",
        "# 云边协同\n\n边缘设备与云端协同调度，自动化装置重新分配任务。\n",
    )
    write_note(rag_vault, "b.md", "# 园艺\n\n番茄和黄瓜的种植方法。\n")
    provider = MockEmbeddingProvider(dimension=64)
    make_index_service(rag_vault, store, provider=provider).rebuild()
    retriever = VectorRetriever(store, provider=provider)
    query = "边缘设备与云端协同调度，自动化装置重新分配任务。"
    vector = asyncio.run(provider.embed_query(query))
    hits = retriever.retrieve(query, top_k=5, query_vector=vector)
    assert hits
    assert hits[0].vector_rank == 1
    assert hits[0].path == "a.md"

    # The lexical answer is a different, exact-term note: the semantic hit is
    # not a keyword coincidence, and the two paths genuinely disagree.
    from server.rag.retrieval.keyword import KeywordRetriever

    lexical = KeywordRetriever(store).retrieve("园艺", top_k=5)
    assert lexical and lexical[0].path == "b.md"
    assert hits[0].path != lexical[0].path


# ----------------------------------------------------------------------
# Hybrid retriever
# ----------------------------------------------------------------------


def _hybrid(store, provider=None, reranker=None, **kwargs) -> HybridRetriever:
    keyword = KeywordRetriever(store)
    vector = VectorRetriever(store, provider=provider) if provider else None
    return HybridRetriever(keyword=keyword, vector=vector, reranker=reranker, **kwargs)


def test_hybrid_works_without_any_embedding_provider(rag_vault, store) -> None:
    write_note(rag_vault, "a.md", "# 云边协同\n\n机械臂重规划任务。\n")
    make_index_service(rag_vault, store).rebuild()
    outcome = _hybrid(store).search("云边协同")
    assert outcome.results
    assert "vector_disabled" in outcome.stats.degraded
    assert outcome.stats.fts_candidates >= 1


def test_hybrid_degrades_when_embeddings_fail(rag_vault, store) -> None:
    write_note(rag_vault, "a.md", "# 云边协同\n\n机械臂重规划任务。\n")
    failing = FailingEmbeddingProvider(dimension=8)
    make_index_service(rag_vault, store, provider=failing).rebuild()
    outcome = _hybrid(store, provider=failing).search("云边协同")
    assert outcome.results  # lexical path still answered
    assert "vector_unavailable" in outcome.stats.degraded


def test_hybrid_combines_both_paths(rag_vault, store) -> None:
    write_note(rag_vault, "a.md", "# 云边协同\n\n机械臂需要在边缘节点重规划任务。\n")
    write_note(rag_vault, "b.md", "# 园艺\n\n番茄种植。\n")
    provider = MockEmbeddingProvider(dimension=64)
    make_index_service(rag_vault, store, provider=provider).rebuild()
    outcome = _hybrid(store, provider=provider).search(
        "云边协同机械臂重规划", include_debug=True
    )
    assert outcome.stats.fts_candidates >= 1
    assert outcome.stats.vector_candidates >= 1
    assert outcome.stats.fused_candidates >= 1
    assert outcome.results[0].path == "a.md"
    assert outcome.stats.debug is not None
    assert outcome.stats.retrieval_ms >= 0


def test_hybrid_fusion_outranks_single_path_hits(rag_vault, store) -> None:
    """A chunk found by both retrievers ranks above one found by only one."""
    write_note(rag_vault, "a.md", "# 云边协同\n\n机械臂重规划。\n")
    provider = MockEmbeddingProvider(dimension=64)
    make_index_service(rag_vault, store, provider=provider).rebuild()
    keyword = KeywordRetriever(store)
    vector = VectorRetriever(store, provider=provider)
    keyword_only = keyword.retrieve("云边协同", top_k=5)
    vector_only = vector.retrieve("云边协同", top_k=5)
    assert keyword_only and vector_only
    overlap = {item.chunk_id for item in keyword_only} & {
        item.chunk_id for item in vector_only
    }
    assert overlap  # the same chunk is found by both paths
    hybrid = _hybrid(store, provider=provider)
    outcome = hybrid.search("云边协同")
    assert outcome.results[0].chunk_id in overlap


def test_hybrid_rerank_is_optional_and_recorded(rag_vault, store) -> None:
    write_note(rag_vault, "a.md", "# 云边协同\n\n机械臂重规划任务细节。\n")
    write_note(rag_vault, "b.md", "# 其他\n\n云边协同的一次会议记录。\n")
    make_index_service(rag_vault, store).rebuild()
    plain = _hybrid(store).search("云边协同 机械臂")
    assert plain.stats.reranked is False
    reranked = _hybrid(store, reranker=LexicalOverlapReranker()).search("云边协同 机械臂")
    assert reranked.stats.reranked is True
    assert any(item.rerank_score is not None for item in reranked.results)


def test_hybrid_reranker_failure_degrades_not_breaks(rag_vault, store) -> None:
    class BrokenReranker:
        name = "broken"

        def rerank(self, query, documents, *, top_n=None):
            raise RuntimeError("reranker exploded")

    write_note(rag_vault, "a.md", "# 云边协同\n\n机械臂。\n")
    make_index_service(rag_vault, store).rebuild()
    outcome = _hybrid(store, reranker=BrokenReranker()).search("云边协同")
    assert outcome.results
    assert "rerank_failed" in outcome.stats.degraded


def test_hybrid_respects_top_k(rag_vault, store) -> None:
    for index in range(6):
        write_note(rag_vault, f"n{index}.md", f"# 笔记{index}\n\n云边协同内容 {index}。\n")
    make_index_service(rag_vault, store).rebuild()
    outcome = _hybrid(store).search("云边协同", top_k=2)
    assert len(outcome.results) == 2


def test_hybrid_allowed_paths_restricts_candidates(rag_vault, store) -> None:
    write_note(rag_vault, "a.md", "# A\n\n云边协同\n")
    write_note(rag_vault, "b.md", "# B\n\n云边协同\n")
    make_index_service(rag_vault, store).rebuild()
    outcome = _hybrid(store).search("云边协同", allowed_paths=["a.md"])
    assert {item.path for item in outcome.results} == {"a.md"}


# ----------------------------------------------------------------------
# Optional link/graph path (roadmap item ③) — off by default
# ----------------------------------------------------------------------


def test_hybrid_without_the_link_path_reports_no_link_state(rag_vault, store) -> None:
    """The pre-③ stack must be byte-identical: no marker, no counter, no stats."""
    write_note(rag_vault, "a.md", "# 云边协同\n\n机械臂重规划任务。\n")
    make_index_service(rag_vault, store).rebuild()
    outcome = _hybrid(store).search("云边协同", include_debug=True)
    assert outcome.results
    assert outcome.stats.link_candidates == 0
    assert not any(marker.startswith("link") for marker in outcome.stats.degraded)
    assert "link_ranked" not in (outcome.stats.debug or {})


def test_hybrid_link_failure_degrades_to_the_direct_paths(rag_vault, store) -> None:
    class BrokenLink:
        name = "link"
        last_degraded = None

        def retrieve(self, query, *, top_k=30, query_vector=None, allowed_paths=None):
            raise RuntimeError("link exploded")

    write_note(rag_vault, "a.md", "# 云边协同\n\n机械臂重规划任务。\n")
    make_index_service(rag_vault, store).rebuild()
    outcome = _hybrid(store, link=BrokenLink()).search("云边协同")
    assert outcome.results
    assert "link_failed" in outcome.stats.degraded


def test_default_knobs_keep_link_candidates_below_every_direct_one(
    rag_vault, store
) -> None:
    """The link list is additive by construction, not by luck.

    With the shipped defaults ``w/(k + link_top_k) < 1/(k + fts_top_k)``, so a
    link-only candidate can never outrank a direct candidate — the property the
    golden-dataset gate relies on (recall/MRR must not drop). A future change to
    these defaults that breaks the inequality fails here instead of silently
    making the link path able to displace direct evidence.
    """
    hybrid = _hybrid(store)
    best_link = hybrid.link_rrf_weight / (hybrid.rrf_k + hybrid.link_top_k)
    worst_direct = 1.0 / (hybrid.rrf_k + hybrid.fts_top_k)
    assert best_link < worst_direct
    assert hybrid.link_add_only is True


def test_lexical_overlap_reranker_orders_by_coverage() -> None:
    reranker = LexicalOverlapReranker()
    items = reranker.rerank(
        "obsidian plugin", ["unrelated text", "obsidian plugin runtime", "obsidian"]
    )
    assert items[0].index == 1
    assert items[0].score > items[-1].score
