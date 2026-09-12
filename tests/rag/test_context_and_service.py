"""Phase 6/7 — ContextBuilder, EvidencePack, grounded generation, citations."""

from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import ValidationError

from server.rag.api_schemas import RagAnswerPayload
from server.rag.citations import (
    NOT_ENOUGH_EVIDENCE,
    ensure_grounded_answer,
    extract_citations,
    is_supported,
    validate_citations,
)
from server.rag.context.builder import (
    ContextBudget,
    RagContextBuilder,
    render_evidence_pack,
)
from server.rag.embeddings.base import FailingEmbeddingProvider, MockEmbeddingProvider
from server.rag.retrieval.hybrid import HybridRetriever
from server.rag.retrieval.keyword import KeywordRetriever
from server.rag.retrieval.vector import VectorRetriever
from server.rag.schemas import EvidencePack, RetrievalResult, SourceEvidence
from server.rag.service import RagService, load_answer_prompt
from tests.rag.conftest import make_index_service, write_note


def _hit(
    chunk_id: str,
    *,
    path: str = "a.md",
    content: str = "content",
    start: int = 1,
    end: int = 5,
    heading: str = "H",
    keyword_rank: int | None = 1,
    vector_rank: int | None = None,
    score: float = 1.0,
) -> RetrievalResult:
    return RetrievalResult(
        chunk_id=chunk_id,
        path=path,
        heading=heading,
        heading_path=heading,
        content=content,
        score=score,
        keyword_rank=keyword_rank,
        vector_rank=vector_rank,
        start_line=start,
        end_line=end,
        content_hash=f"hash::{chunk_id}",
    )


# ----------------------------------------------------------------------
# ContextBuilder
# ----------------------------------------------------------------------


def test_builder_numbers_sources_and_keeps_provenance() -> None:
    pack = RagContextBuilder().build(
        "问题",
        [
            _hit("c1", path="a.md", content="第一段", start=3, end=6, heading="A"),
            _hit("c2", path="b.md", content="第二段", start=10, end=12, heading="B"),
        ],
    )
    assert pack.query == "问题"
    assert [s.source_id for s in pack.sources] == ["S1", "S2"]
    assert pack.sources[0].path == "a.md"
    assert pack.sources[0].start_line == 3 and pack.sources[0].end_line == 6
    assert pack.sources[0].heading == "A"
    assert pack.sources[0].excerpt == "第一段"
    assert pack.candidate_count == 2
    assert pack.context_tokens > 0


def test_builder_deduplicates_same_chunk_and_same_text() -> None:
    pack = RagContextBuilder().build(
        "q",
        [
            _hit("c1", content="same text"),
            _hit("c1", content="same text"),
            _hit("c2", content="same text"),
        ],
    )
    assert len(pack.sources) == 1


def test_builder_merges_adjacent_chunks_of_one_document() -> None:
    pack = RagContextBuilder().build(
        "q",
        [
            _hit("c1", path="a.md", content="第一段", start=1, end=5),
            _hit("c2", path="a.md", content="第二段", start=6, end=10),
            _hit("c3", path="b.md", content="另一篇", start=1, end=3),
        ],
    )
    assert len(pack.sources) == 2
    merged = pack.sources[0]
    assert merged.start_line == 1 and merged.end_line == 10
    assert "第一段" in merged.content and "第二段" in merged.content
    assert merged.path == "a.md"


def test_builder_does_not_merge_distant_chunks() -> None:
    pack = RagContextBuilder(
        ContextBudget(merge_gap_lines=1, max_chunks=5)
    ).build(
        "q",
        [
            _hit("c1", path="a.md", content="A", start=1, end=2),
            _hit("c2", path="a.md", content="B", start=40, end=50),
        ],
    )
    assert len(pack.sources) == 2


def test_builder_respects_max_chunks() -> None:
    pack = RagContextBuilder(ContextBudget(max_chunks=2)).build(
        "q",
        [_hit(f"c{i}", path=f"{i}.md", content=f"text {i}") for i in range(6)],
    )
    assert len(pack.sources) == 2
    assert pack.truncated is True


def test_builder_respects_the_per_document_token_budget() -> None:
    """A chunk larger than a document's share is skipped, not smuggled in."""
    long_text = "很长的中文内容。" * 200
    pack = RagContextBuilder(
        ContextBudget(
            max_chunks=6,
            max_tokens=4000,
            max_tokens_per_document=200,
            merge_adjacent=False,
        )
    ).build("q", [_hit("c1", path="a.md", content=long_text)])
    # The first block of a document is always admitted (an answer needs at
    # least one citable block) and the document is then closed, so the pack is
    # bounded no matter how long the note is.
    assert [source.path for source in pack.sources] == ["a.md"]
    assert pack.truncated is True


def test_builder_stops_at_the_total_token_budget() -> None:
    text = "中文段落内容。" * 20  # ~140 tokens per block
    pack = RagContextBuilder(
        ContextBudget(
            max_chunks=5,
            max_tokens=400,
            max_tokens_per_document=400,
            merge_adjacent=False,
        )
    ).build("q", [_hit(f"c{i}", path=f"{i}.md", content=text) for i in range(5)])
    assert pack.context_tokens <= 400
    assert 1 <= len(pack.sources) < 5
    assert pack.truncated is True


def test_builder_caps_per_document_budget() -> None:
    big = "中文段落内容。" * 150
    pack = RagContextBuilder(
        ContextBudget(
            max_chunks=6,
            max_tokens=20_000,
            max_tokens_per_document=200,
            merge_adjacent=False,
        )
    ).build(
        "q",
        [
            _hit("a1", path="big.md", content=big),
            _hit("a2", path="big.md", content=big),
            _hit("a3", path="big.md", content=big),
            _hit("b1", path="small.md", content="小内容"),
        ],
    )
    paths = [source.path for source in pack.sources]
    assert paths.count("big.md") == 1  # the long note contributes exactly one
    assert "small.md" in paths


def test_builder_empty_results_produce_empty_pack() -> None:
    pack = RagContextBuilder().build("q", [])
    assert pack.sources == []
    assert pack.context_tokens == 0


def test_render_evidence_pack_is_numbered_and_labelled() -> None:
    pack = RagContextBuilder().build(
        "q",
        [
            _hit(
                "c1",
                path="AI/RAG.md",
                content="正文内容",
                start=32,
                end=51,
                heading="Hybrid Search",
            )
        ],
    )
    rendered = render_evidence_pack(pack)
    assert "[S1]" in rendered
    assert "path: AI/RAG.md" in rendered
    assert "heading: Hybrid Search" in rendered
    assert "lines: 32-51" in rendered
    assert "正文内容" in rendered


# ----------------------------------------------------------------------
# Citation validation
# ----------------------------------------------------------------------


def _pack(*ids: str) -> EvidencePack:
    return EvidencePack(
        query="q",
        sources=[
            SourceEvidence(
                source_id=source_id,
                path=f"{source_id}.md",
                heading="H",
                start_line=1,
                end_line=2,
                content="content",
            )
            for source_id in ids
        ],
    )


def test_extract_citations_finds_all_markers() -> None:
    assert extract_citations("见 [S1] 与 [s2]，不是 [S10]") == ["S1", "S2", "S10"]


def test_validate_citations_keeps_valid_and_drops_invalid() -> None:
    sanitized, report, used = validate_citations(
        "结论 [S1]，另外 [S99] 是编的。", _pack("S1", "S2")
    )
    assert "[S1]" in sanitized
    assert "S99" not in sanitized
    assert report.invalid_ids == ["S99"]
    assert [source.source_id for source in used] == ["S1"]


def test_validate_citations_reports_resolved_ids() -> None:
    _, report, used = validate_citations("A [S2] B [S2]", _pack("S1", "S2"))
    assert report.resolved_ids == ["S2"]
    assert len(used) == 1


def test_validate_citations_sources_come_from_pack_not_model() -> None:
    """A model-invented path can never appear in the validated source list."""
    sanitized, report, used = validate_citations(
        "见 [S1]，文件 Secret/Plan.md 里说了……", _pack("S1")
    )
    assert all(source.path == "S1.md" for source in used)
    assert "[S1]" in sanitized
    assert report.invalid_ids == []


def test_is_supported_requires_a_real_citation() -> None:
    assert is_supported("answer [S1]", _pack("S1")) is True
    assert is_supported("answer without markers", _pack("S1")) is False
    assert is_supported("answer [S9]", _pack("S1")) is False


def test_ensure_grounded_answer_replaces_unsupported_prose() -> None:
    answer, report, sources, grounded = ensure_grounded_answer(
        "我的笔记里说过很多内容，但没有引用。", _pack("S1")
    )
    assert answer == NOT_ENOUGH_EVIDENCE
    assert sources == []
    assert grounded is False


def test_ensure_grounded_answer_with_empty_pack() -> None:
    answer, _, sources, grounded = ensure_grounded_answer("随便说点什么", _pack())
    assert answer == NOT_ENOUGH_EVIDENCE
    assert sources == [] and grounded is False


def test_ensure_grounded_answer_passes_grounded_prose() -> None:
    answer, report, sources, grounded = ensure_grounded_answer(
        "第一，采用了双系统架构 [S1]。", _pack("S1")
    )
    assert grounded is True
    assert "[S1]" in answer
    assert [source.source_id for source in sources] == ["S1"]


# ----------------------------------------------------------------------
# RAG service
# ----------------------------------------------------------------------


class _EmptyKeywordRetriever:
    """A keyword retriever that finds nothing (used to pin degradation tests)."""

    name = "keyword"

    def retrieve(self, query, *, top_k=30, query_vector=None, allowed_paths=None):
        return []


def _service(rag_vault, store, provider=None, generate=None, **kwargs) -> RagService:
    provider = provider or MockEmbeddingProvider(dimension=64)
    index = make_index_service(rag_vault, store, provider=provider)
    index.rebuild()
    vector = VectorRetriever(store, provider=provider)
    retriever = HybridRetriever(keyword=KeywordRetriever(store), vector=vector, **kwargs)
    return RagService(index=index, retriever=retriever, generate=generate)


def test_service_search_never_calls_the_chat_model(rag_vault, store) -> None:
    calls: list[str] = []

    async def generate(system, user):  # pragma: no cover - must not be called
        calls.append(user)
        return "{}", "chat-model"

    write_note(rag_vault, "a.md", "# 云边协同\n\n机械臂重规划任务。\n")
    service = _service(rag_vault, store, generate=generate)
    response = service.search("云边协同", top_k=5)
    assert response.results
    assert calls == []
    assert response.results[0].path == "a.md"
    assert response.results[0].excerpt
    assert response.stats.fts_candidates >= 1


def test_service_search_rejects_empty_query(rag_vault, store) -> None:
    from server.rag.errors import RagInvalidRequest

    service = _service(rag_vault, store)
    with pytest.raises(RagInvalidRequest):
        service.search("   ")


def test_prompt_is_a_loadable_markdown_prompt() -> None:
    prompt = load_answer_prompt()
    assert "ONLY the numbered evidence blocks" in prompt
    assert "[S1]" in prompt
    assert "没有找到足够证据" in prompt


def test_service_query_grounds_the_answer(rag_vault, store) -> None:
    write_note(
        rag_vault,
        "研究/云边协同.md",
        "# 云边协同\n\n## 重规划机制\n\n边缘节点在时延超限时重新分配任务。\n",
    )
    write_note(rag_vault, "园艺.md", "# 园艺\n\n番茄种植方法。\n")

    async def generate(system, user):
        assert "[S1]" in user  # the model sees the numbered evidence only
        return json.dumps(
            {"answer": "你的笔记采用了重规划机制 [S1]。", "used_sources": ["S1"]}
        ), "qwen-test"

    service = _service(rag_vault, store, generate=generate)
    response = asyncio.run(service.query("云边协同的重规划机制"))
    assert response.sources
    assert response.sources[0].path == "研究/云边协同.md"
    assert response.sources[0].start_line >= 1
    assert "[S1]" in response.answer
    assert response.model == "qwen-test"
    assert response.prompt_version.startswith("rag_answer@")
    assert response.retrieval_stats.context_chunks >= 1


def test_service_query_strips_hallucinated_citations(rag_vault, store) -> None:
    write_note(rag_vault, "a.md", "# 云边协同\n\n机械臂重规划。\n")

    async def generate(system, user):
        return json.dumps(
            {
                "answer": "根据 [S1] 的记录，另外 [S42] 也有说明。",
                "used_sources": ["S1", "S42"],
            }
        ), "m"

    service = _service(rag_vault, store, generate=generate)
    response = asyncio.run(service.query("云边协同"))
    assert "S42" not in response.answer
    assert "S42" in response.invalid_citations
    assert all(source.id in {"S1"} for source in response.sources)
    assert len(response.sources) == 1


def test_service_query_without_evidence_does_not_call_the_model(
    rag_vault, store
) -> None:
    calls: list[str] = []

    async def generate(system, user):  # pragma: no cover
        calls.append(user)
        return "{}", "m"

    write_note(rag_vault, "a.md", "# 园艺\n\n番茄种植方法。\n")
    service = _service(rag_vault, store, generate=generate)
    # Both paths are pinned to "no evidence": the vector floor is a configured
    # threshold (see DEFAULT_MIN_SCORE) and the lexical fallback is disabled, so
    # the test measures the service's behaviour and not retrieval luck.
    service._retriever._vector.min_score = 0.99  # noqa: SLF001
    service._retriever._keyword = _EmptyKeywordRetriever()  # noqa: SLF001
    response = asyncio.run(service.query("quantum gardening migration patterns"))
    assert calls == []
    assert response.sources == []
    assert response.answer == NOT_ENOUGH_EVIDENCE


def test_service_query_unstructured_answer_is_kept_but_validated(
    rag_vault, store
) -> None:
    write_note(rag_vault, "a.md", "# 云边协同\n\n机械臂重规划。\n")

    async def generate(system, user):
        return "这是一段自然语言回答 [S1]。", "m"

    service = _service(rag_vault, store, generate=generate)
    response = asyncio.run(service.query("云边协同"))
    assert "generation_unstructured" in response.degraded
    assert response.sources and response.sources[0].id == "S1"


def test_service_query_degrades_when_generation_fails(rag_vault, store) -> None:
    write_note(rag_vault, "a.md", "# 云边协同\n\n机械臂重规划。\n")

    async def generate(system, user):
        raise RuntimeError("model exploded")

    service = _service(rag_vault, store, generate=generate)
    response = asyncio.run(service.query("云边协同"))
    assert "generation_failed" in response.degraded
    assert response.answer  # an explicit degradation message, not a crash
    # The evidence is real even though the model failed, so it is still cited.
    assert [source.path for source in response.sources] == ["a.md"]


def test_service_query_without_generator_returns_evidence_list(
    rag_vault, store
) -> None:
    write_note(rag_vault, "a.md", "# 云边协同\n\n机械臂重规划。\n")
    service = _service(rag_vault, store, generate=None)
    response = asyncio.run(service.query("云边协同"))
    assert "generation_unavailable" in response.degraded
    assert "a.md" in response.answer


def test_service_query_degrades_when_embeddings_fail(rag_vault, store) -> None:
    write_note(rag_vault, "a.md", "# 云边协同\n\n机械臂重规划。\n")
    failing = FailingEmbeddingProvider(dimension=8)
    service = _service(rag_vault, store, provider=failing)
    response = asyncio.run(service.query("云边协同"))
    assert "vector_unavailable" in response.degraded
    assert response.sources  # lexical evidence still grounds the answer


def test_service_status_reports_degraded_embedding(rag_vault, store) -> None:
    from server.rag.embeddings.base import HashEmbeddingProvider

    write_note(rag_vault, "a.md", "# A\n\ncontent\n")
    service = _service(rag_vault, store, provider=HashEmbeddingProvider(dimension=32))
    status = service.status()
    assert status.enabled is True
    assert status.embedding_degraded is True
    assert status.chunks >= 1
    assert status.indexed_notes == 1
    assert status.vector_store == "sqlite"
    assert status.message


def test_rerank_flag_can_be_disabled_per_request(rag_vault, store) -> None:
    from server.rag.rerank.base import LexicalOverlapReranker

    write_note(rag_vault, "a.md", "# 云边协同\n\n机械臂重规划。\n")
    service = _service(rag_vault, store, reranker=LexicalOverlapReranker())
    with_rerank = service.search("云边协同", top_k=3)
    assert with_rerank.stats.reranked is True
    without = service._retriever.search("云边协同", top_k=3, rerank=False)  # noqa: SLF001
    assert without.stats.reranked is False


def test_answer_payload_schema_is_strict() -> None:
    payload = RagAnswerPayload.model_validate(
        {"answer": "x", "used_sources": ["S1"]}
    )
    assert payload.used_sources == ["S1"]
    with pytest.raises(ValidationError):
        RagAnswerPayload.model_validate({"answer": "x", "unknown": 1})


def test_service_query_returns_debug_when_requested(rag_vault, store) -> None:
    write_note(rag_vault, "a.md", "# 云边协同\n\n机械臂重规划。\n")
    service = _service(rag_vault, store)
    response = asyncio.run(service.query("云边协同", include_debug=True))
    assert response.retrieval_stats.retrieval_debug is not None
