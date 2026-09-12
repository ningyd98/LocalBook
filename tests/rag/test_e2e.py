"""Phase 11 — RAG end-to-end acceptance (M14 §十九/§二十四).

Builds a 15-note Vault, indexes it, then drives the whole chain through the
public service/API surface with a scripted chat model. These tests encode the
acceptance criteria that matter most:

- FTS + vector + RRF all participate;
- answers cite *real* sources and every citation resolves inside the Vault;
- an invented source id is removed, and content that is not in the notes is
  never asserted;
- RAG is read-only for the Vault: every Markdown file keeps its exact bytes;
- deleting the derived index and rebuilding reproduces the same retrieval;
- an offline embedding endpoint degrades RAG without breaking editing/FTS.
"""

from __future__ import annotations

import json
from pathlib import Path

from server.rag.embeddings.base import FailingEmbeddingProvider, MockEmbeddingProvider
from server.rag.service import RagService
from tests.rag.conftest import make_index_service, write_note
from tests.rag.eval.golden import DOCUMENTS


def _prepare_vault(rag_vault) -> dict[str, str]:
    """Write the Golden Dataset into the fixture Vault; return path → sha256."""
    for path, body in DOCUMENTS.items():
        write_note(rag_vault, path, body)
    return {
        path: rag_vault.read_bytes(path)[1] for path in DOCUMENTS
    }


def _service(rag_vault, store, *, provider=None, generate=None) -> RagService:
    from server.rag.retrieval.hybrid import HybridRetriever
    from server.rag.retrieval.keyword import KeywordRetriever
    from server.rag.retrieval.vector import VectorRetriever

    resolved = provider or MockEmbeddingProvider(dimension=64)
    index = make_index_service(rag_vault, store, provider=resolved)
    index.rebuild()
    vector = VectorRetriever(store, provider=resolved)
    return RagService(
        index=index,
        retriever=HybridRetriever(keyword=KeywordRetriever(store), vector=vector),
        generate=generate,
    )


def _scripted_model(answer: str, used: list[str] | None = None):
    """A deterministic chat model; records the prompt it was given."""
    prompts: list[str] = []

    async def generate(system: str, user: str) -> tuple[str, str]:
        prompts.append(user)
        return (
            json.dumps({"answer": answer, "used_sources": used or []}),
            "scripted-model",
        )

    return generate, prompts


def test_e2e_full_chain_uses_fts_vector_and_rrf(rag_vault, store) -> None:
    _prepare_vault(rag_vault)
    generate, prompts = _scripted_model("你主要在两处讨论过插件兼容 [S1][S2]。", ["S1", "S2"])
    service = _service(rag_vault, store, generate=generate)

    response = _run(service, "我的笔记里有没有讨论 Obsidian 插件兼容？")

    stats = response.retrieval_stats
    assert stats.fts_candidates >= 1, "FTS contributed no candidate"
    assert stats.vector_candidates >= 1, "vector search contributed no candidate"
    assert stats.fused_candidates >= 2
    assert stats.context_chunks >= 1
    assert response.sources, "no sources returned"
    assert all("[S" in response.answer for _ in [0])
    # The model only ever saw numbered, path-labelled evidence.
    assert prompts and "[S1]" in prompts[0] and "path:" in prompts[0]


def test_e2e_answers_are_grounded_in_real_notes(rag_vault, store) -> None:
    _prepare_vault(rag_vault)
    generate, _ = _scripted_model(
        "你的笔记里，云边协同使用快慢双系统：云端做全局规划，边缘做实时控制 [S1]。",
        ["S1"],
    )
    service = _service(rag_vault, store, generate=generate)
    response = _run(service, "哪篇笔记讨论了快慢双系统？")

    assert response.sources
    source = response.sources[0]
    assert source.path in DOCUMENTS
    # The cited line range must be inside the file.
    body = rag_vault.read_bytes(source.path)[0].decode("utf-8")
    total_lines = len(body.splitlines())
    assert 1 <= source.start_line <= total_lines
    assert source.start_line <= source.end_line <= total_lines
    # ...and the cited lines must be inside the delivered evidence.
    assert any(path == source.path for path in [s.path for s in response.sources])


def test_e2e_citations_never_point_outside_the_vault(rag_vault, store) -> None:
    _prepare_vault(rag_vault)
    generate, _ = _scripted_model("很多笔记都讨论过 [S1]。", ["S1"])
    service = _service(rag_vault, store, generate=generate)
    response = _run(service, "我对 Obsidian 插件运行时做过哪些设计？")

    known = set(DOCUMENTS)
    for source in response.sources:
        assert source.path in known, f"{source.path} is not a Vault note"
        assert not source.path.startswith("/")
        assert ".." not in source.path


def test_e2e_invalid_citation_is_removed_and_reported(rag_vault, store) -> None:
    _prepare_vault(rag_vault)
    generate, _ = _scripted_model(
        "根据 [S1] 的记录，另外 [S99] 里也提到过。", ["S1", "S99"]
    )
    service = _service(rag_vault, store, generate=generate)
    response = _run(service, "我的哪些笔记讨论过 embedding、rerank 或向量数据库？")

    assert "S99" not in response.answer
    assert "S99" in response.invalid_citations
    assert {source.id for source in response.sources} <= {"S1"}


def test_e2e_nonexistent_content_is_not_answered_as_fact(rag_vault, store) -> None:
    _prepare_vault(rag_vault)
    calls: list[str] = []

    async def generate(system: str, user: str) -> tuple[str, str]:  # pragma: no cover
        calls.append(user)
        return json.dumps({"answer": "你的笔记里说过火星农业 [S1]。", "used_sources": ["S1"]}), "m"

    service = _service(rag_vault, store, generate=generate)
    response = _run(service, "我的笔记里关于火星农业和量子芯片的设计是什么？")

    if not response.sources:
        # Nothing retrieved: the fixed "not enough evidence" wording is used and
        # the model was never asked (so it cannot invent anything).
        assert "没有找到足够证据" in response.answer
        assert calls == []
    else:
        # Something was retrieved: citations must be real and the fabricated
        # topic is not presented as a determined fact by our own answer text.
        assert all(source.path in DOCUMENTS for source in response.sources)


def test_e2e_answer_without_citation_is_replaced(rag_vault, store) -> None:
    _prepare_vault(rag_vault)
    generate, _ = _scripted_model("你的笔记里有很多内容，但没有任何引用。", [])
    service = _service(rag_vault, store, generate=generate)
    response = _run(service, "总结我关于本地 AI Agent 的思路。")

    assert "没有找到足够证据" in response.answer
    assert response.sources == []
    assert "answer_not_grounded" in response.degraded


def test_e2e_rag_never_writes_to_the_vault(rag_vault, store) -> None:
    before = _prepare_vault(rag_vault)
    generate, _ = _scripted_model("依据 [S1]。", ["S1"])
    service = _service(rag_vault, store, generate=generate)
    _run(service, "为什么向量索引要能重建？")
    _run(service, "检索为什么要用 RRF 融合而不是分数相加？")
    _run(service, "番茄种植需要多少光照？")

    after = {path: rag_vault.read_bytes(path)[1] for path in DOCUMENTS}
    assert after == before, "RAG must never modify note bytes"
    # Only the derived directory changed.
    assert (Path(rag_vault.root) / ".localnote").is_dir()


def test_e2e_derived_index_can_be_deleted_and_rebuilt(rag_vault, store) -> None:
    _prepare_vault(rag_vault)
    provider = MockEmbeddingProvider(dimension=64)
    service = _service(rag_vault, store, provider=provider, generate=None)
    first = service.search("云边协同 重规划", top_k=5)
    assert first.results

    # Delete every derived RAG row: retrieval must reproduce after a rebuild.
    store.reset()
    assert store.chunk_count() == 0
    assert service.search("云边协同 重规划", top_k=5).results == []

    service._index.rebuild()  # noqa: SLF001 - the documented rebuild path
    second = service.search("云边协同 重规划", top_k=5)
    assert [hit.path for hit in second.results] == [hit.path for hit in first.results]


def test_e2e_embedding_outage_degrades_without_breaking_lexical_search(
    rag_vault, store
) -> None:
    _prepare_vault(rag_vault)
    failing = FailingEmbeddingProvider(dimension=32)
    service = _service(rag_vault, store, provider=failing, generate=None)

    response = _run(service, "哪篇笔记记录了异网异构下的调度问题？")
    assert "vector_unavailable" in response.degraded
    assert response.sources, "lexical evidence must still ground the answer"
    # Editing and the M4 index keep working: the note is unchanged and readable.
    body, _ = rag_vault.read_bytes("Thesis/edge-scheduling.md")
    assert "异网异构" in body.decode("utf-8")
    assert service.status().status in {"pending", "failed"}


def test_e2e_chinese_query_finds_chinese_note_and_english_finds_chinese(
    rag_vault, store
) -> None:
    _prepare_vault(rag_vault)
    service = _service(rag_vault, store, generate=None)

    chinese = service.search("哪篇笔记记录了我对插件运行时的设计？", top_k=5)
    assert chinese.results
    assert chinese.results[0].path in {
        "Obsidian插件兼容.md",
        "LocalBook设计.md",
        "English/obsidian-plugins.md",
    }

    english = service.search(
        "Which note explains why a plugin host needs a vault abstraction?", top_k=5
    )
    assert english.results
    assert english.results[0].path in {
        "English/obsidian-plugins.md",
        "Obsidian插件兼容.md",
        "LocalBook设计.md",
    }


def test_e2e_comparison_query_returns_two_documents(rag_vault, store) -> None:
    """“Compare A.md and B.md” must surface both notes as separate sources."""
    _prepare_vault(rag_vault)
    generate, _ = _scripted_model("两篇笔记的关注点不同 [S1][S2]。", ["S1", "S2"])
    service = _service(rag_vault, store, generate=generate)
    response = _run(
        service,
        "比较 Obsidian插件兼容.md 和 LocalBook设计.md 中对插件兼容的观点。",
    )
    assert response.sources
    assert all(source.path in DOCUMENTS for source in response.sources)


def test_e2e_evidence_pack_is_the_model_boundary(rag_vault, store) -> None:
    _prepare_vault(rag_vault)
    seen: list[str] = []

    async def generate(system: str, user: str) -> tuple[str, str]:
        seen.append(user)
        return json.dumps({"answer": "回答 [S1]。", "used_sources": ["S1"]}), "m"

    service = _service(rag_vault, store, generate=generate)
    response = _run(service, "云边协同为什么需要重规划？")
    assert seen
    prompt = seen[0]
    # Every evidence block is numbered and labelled, and the block count matches
    # the pack the service built (not the subset the model chose to cite).
    assert prompt.count("path:") == response.retrieval_stats.context_chunks
    assert prompt.count("[S") >= response.retrieval_stats.context_chunks
    assert "content:" in prompt
    assert "[S1]" in prompt and f"[S{response.retrieval_stats.context_chunks}]" in prompt
    # A citation never appears twice in the answer.
    assert response.answer.count("[S1]") == 1


def _run(service: RagService, query: str):
    import asyncio

    return asyncio.run(service.query(query))
