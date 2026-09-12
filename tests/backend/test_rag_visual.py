"""Visual RAG response contract tests."""
from __future__ import annotations

pytest_plugins = ("tests.rag.conftest",)

import asyncio
import json

from server.rag.service import RagService
from tests.rag.conftest import make_index_service, write_note
from server.rag.embeddings.base import MockEmbeddingProvider
from server.rag.retrieval.hybrid import HybridRetriever
from server.rag.retrieval.keyword import KeywordRetriever
from server.rag.retrieval.vector import VectorRetriever


def _service(vault, store, generate=None):
    provider = MockEmbeddingProvider(dimension=32)
    index = make_index_service(vault, store, provider=provider)
    index.rebuild()
    vector = VectorRetriever(store, provider=provider)
    retriever = HybridRetriever(keyword=KeywordRetriever(store), vector=vector)
    return RagService(index=index, retriever=retriever, generate=generate)


def test_search_exposes_visual_rank_and_source_binding(rag_vault, store):
    write_note(rag_vault, "a.md", "# Alpha\n\n云边协同重规划。\n")
    response = _service(rag_vault, store).search("云边协同")
    assert response.results
    hit = response.results[0]
    assert hit.rank == 1
    assert hit.source_id == hit.chunk_id
    assert hit.start_line >= 1 <= hit.end_line
    assert response.stats.link_candidates >= 0


def test_query_evidence_summary_is_server_derived(rag_vault, store):
    write_note(rag_vault, "a.md", "# Alpha\n\n云边协同重规划。\n")

    async def generate(system, user):
        return json.dumps({"answer": "结论 [S1]", "used_sources": ["S1"]}), "test"

    response = asyncio.run(_service(rag_vault, store, generate).query("云边协同"))
    assert response.evidence["source_count"] == len(response.sources) == 1
    assert response.evidence["paths"] == ["a.md"]
    assert response.evidence["grounded"] is True
    assert response.evidence["candidate_count"] >= 1


def test_query_generation_degradation_keeps_evidence(rag_vault, store):
    write_note(rag_vault, "a.md", "# Alpha\n\n云边协同重规划。\n")

    async def generate(system, user):
        raise RuntimeError("offline")

    response = asyncio.run(_service(rag_vault, store, generate).query("云边协同"))
    assert "generation_failed" in response.degraded
    assert response.sources and response.evidence["grounded"] is True
    assert "a.md" in response.answer


def test_query_empty_results_have_stable_empty_evidence(rag_vault, store):
    write_note(rag_vault, "a.md", "# Gardening\n\n番茄种植。\n")
    service = _service(rag_vault, store)
    service._retriever._vector.min_score = 0.99  # noqa: SLF001
    service._retriever._keyword = type("EmptyKeyword", (), {"retrieve": lambda *args, **kwargs: []})()  # noqa: SLF001
    response = asyncio.run(service.query("quantum migration"))
    assert response.sources == []
    assert response.evidence.get("source_count", 0) == 0
    assert response.evidence.get("grounded", False) is False


def test_query_drops_model_fabricated_citations(rag_vault, store):
    write_note(rag_vault, "a.md", "# Alpha\n\n云边协同重规划。\n")

    async def generate(system, user):
        return json.dumps({"answer": "结论 [S1] [S99]", "used_sources": ["S1", "S99"]}), "test"

    response = asyncio.run(_service(rag_vault, store, generate).query("云边协同"))
    assert "S99" in response.invalid_citations
    assert "S99" not in response.answer
    assert all(source.id == "S1" for source in response.sources)
