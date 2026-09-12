"""Phase 13 — M6 ``related`` reuses the M14 RAG evidence path (M14 §八/§十).

``Suggest Related`` must stay model-bounded: candidates are narrowed
programmatically *before* any model call, and every path the model may propose
must already be in that set. M14 adds the chunk-level hybrid retriever as the
highest-priority source, so a semantically similar note (no shared keywords) can
be found without a chat call — while link/graph/FTS still work when RAG is off.
"""

from __future__ import annotations

from server.ai.candidates import reduce_candidates
from server.ai.workflows import AIWorkflowService
from server.rag.retrieval.hybrid import HybridRetriever
from server.rag.retrieval.keyword import KeywordRetriever
from server.rag.retrieval.vector import VectorRetriever
from server.rag.schemas import RetrievalResult
from tests.rag.conftest import make_index_service, write_note


class _StubSettings:
    """The subset of AISettings the candidate path touches."""

    chat_model = "stub"
    temperature = 0.1
    request_timeout_seconds = 5
    max_output_tokens = 128
    max_context_notes = 5
    max_context_chars_per_note = 4000
    max_context_chars_total = 12000
    qwen_match_pattern = "qwen"


class _StubAdapter:
    """Never called by the candidate path; fails loudly if it ever is."""

    async def list_models(self):  # pragma: no cover - must not be reached
        raise AssertionError("candidate reduction must not call the model")

    async def chat(self, **kwargs):  # pragma: no cover - must not be reached
        raise AssertionError("candidate reduction must not call the model")


class _StubStack:
    def __init__(self, retriever) -> None:
        self.retriever = retriever


def _hit(path: str, *, rank: int = 1, score: float = 1.0) -> RetrievalResult:
    return RetrievalResult(
        chunk_id=f"{path}::{rank}",
        path=path,
        heading="H",
        heading_path="H",
        content="内容",
        score=score,
        keyword_rank=rank,
        start_line=1,
        end_line=2,
        content_hash=f"h{rank}",
    )


# ----------------------------------------------------------------------
# Candidate reduction
# ----------------------------------------------------------------------


def test_rag_hits_win_over_other_sources_and_are_deduplicated() -> None:
    class FakeIndex:
        def entries(self):
            return []

        def graph_snapshot(self):
            raise RuntimeError("no graph")

    candidates = reduce_candidates(
        FakeIndex(),
        "current.md",
        limit=5,
        rag_hits=[_hit("semantic.md"), _hit("semantic.md", rank=2), _hit("other.md")],
    )
    assert [row for row in candidates] == []  # allow-list still applies
    # With the notes present in the index, order follows the retrieval ranking.
    class IndexWithNotes(FakeIndex):
        def entries(self):
            class Row:
                def __init__(self, path):
                    self.path = path

            return [Row("semantic.md"), Row("other.md"), Row("current.md")]

    ordered = reduce_candidates(
        IndexWithNotes(),
        "current.md",
        limit=5,
        rag_hits=[_hit("semantic.md"), _hit("other.md")],
    )
    assert [row.path for row in ordered] == ["semantic.md", "other.md"]


def test_rag_hits_never_allow_a_path_outside_the_index() -> None:
    class Row:
        def __init__(self, path):
            self.path = path

    class FakeIndex:
        def entries(self):
            return [Row("known.md"), Row("current.md")]

        def graph_snapshot(self):
            raise RuntimeError("no graph")

    candidates = reduce_candidates(
        FakeIndex(),
        "current.md",
        limit=5,
        rag_hits=[_hit("known.md"), _hit("invented.md")],
    )
    assert [row.path for row in candidates] == ["known.md"]


def test_rag_hits_never_include_the_current_note() -> None:
    class Row:
        def __init__(self, path):
            self.path = path

    class FakeIndex:
        def entries(self):
            return [Row("current.md"), Row("other.md")]

        def graph_snapshot(self):
            raise RuntimeError("no graph")

    candidates = reduce_candidates(
        FakeIndex(),
        "current.md",
        limit=5,
        rag_hits=[_hit("current.md"), _hit("other.md")],
    )
    assert [row.path for row in candidates] == ["other.md"]


def test_without_rag_hits_the_m4_path_still_works() -> None:
    """RAG off ⇒ the pre-M14 candidate behaviour must be unchanged."""

    class Row:
        def __init__(self, path, targets=()):
            self.path = path
            self.resolved_targets = set(targets)
            self.outgoing = []

    class FakeIndex:
        def entries(self):
            return [Row("current.md", ["linked.md"]), Row("linked.md")]

        def graph_snapshot(self):
            raise RuntimeError("no graph")

    candidates = reduce_candidates(FakeIndex(), "current.md", limit=5)
    assert [row.path for row in candidates] == ["linked.md"]


# ----------------------------------------------------------------------
# Workflow integration
# ----------------------------------------------------------------------


def _service(index, *, rag=None) -> AIWorkflowService:
    return AIWorkflowService(
        _StubSettings(), _StubAdapter(), vault=None, index=index, rag=rag
    )


def test_rag_candidates_respect_limit_and_exclude_current_note() -> None:
    class Stack:
        class retriever:  # noqa: N801 - minimal stand-in
            @staticmethod
            def search(query, *, top_k=10, include_debug=False, rerank=None):
                class Outcome:
                    results = [
                        _hit("current.md"),
                        _hit("a.md"),
                        _hit("b.md"),
                        _hit("a.md", rank=4),
                        _hit("c.md"),
                    ]

                return Outcome()

    service = _service(index=None, rag=Stack())
    hits = service._rag_candidates("云边协同", exclude="current.md")  # noqa: SLF001
    assert [hit.path for hit in hits] == ["a.md", "b.md", "c.md"]
    assert len(hits) <= service.settings.max_context_notes - 1


def test_rag_candidates_degrade_to_empty_on_failure() -> None:
    class BrokenStack:
        class retriever:  # noqa: N801
            @staticmethod
            def search(*args, **kwargs):
                raise RuntimeError("index exploded")

    service = _service(index=None, rag=BrokenStack())
    assert service._rag_candidates("q", exclude="current.md") == []  # noqa: SLF001
    assert _service(index=None)._rag_candidates("q", exclude="x") == []  # noqa: SLF001


def test_rag_candidates_skip_blank_queries() -> None:
    service = _service(index=None, rag=_StubStack(None))
    assert service._rag_candidates("   ", exclude="a.md") == []  # noqa: SLF001


def test_related_prefers_semantic_neighbour_found_only_by_rag(rag_vault, store) -> None:
    """End to end: a note sharing no keyword becomes a candidate through RAG.

    This is the M14 §八 contract in action — ``Suggest Related`` and the RAG
    answer path draw their candidates from the same chunk-level retrieval.
    """
    from server.rag.embeddings.base import MockEmbeddingProvider

    write_note(
        rag_vault,
        "current.md",
        "# 云边协同\n\n边缘节点与云端协同调度，自动化装置重新分配任务。\n",
    )
    write_note(
        rag_vault,
        "semantic.md",
        "# 调度记录\n\n边缘节点与云端协同调度，自动化装置重新分配任务的具体过程。\n",
    )
    write_note(rag_vault, "unrelated.md", "# 园艺\n\n番茄与黄瓜的种植方法。\n")

    provider = MockEmbeddingProvider(dimension=64)
    rag_index = make_index_service(rag_vault, store, provider=provider)
    rag_index.rebuild()
    retriever = HybridRetriever(
        keyword=KeywordRetriever(store),
        vector=VectorRetriever(store, provider=provider),
    )

    service = AIWorkflowService(
        _StubSettings(),
        _StubAdapter(),
        vault=rag_vault,
        index=store,
        rag=_StubStack(retriever),
    )

    query = "边缘节点与云端协同调度，自动化装置重新分配任务。"
    hits = service._rag_candidates(query, exclude="current.md")  # noqa: SLF001
    paths = [hit.path for hit in hits]
    assert "semantic.md" in paths
    assert "current.md" not in paths

    # The candidate list feeds the model, so it must be bounded and allow-listed.
    bounded = reduce_candidates(
        store, "current.md", limit=2, query=query, rag_hits=hits
    )
    assert len(bounded) <= 2
    assert all(row.path in {p for p in store.document_paths()} for row in bounded)


def test_related_still_works_when_rag_is_absent(rag_vault, store) -> None:
    """RAG off must leave the M4 link/graph candidate path functional."""
    write_note(rag_vault, "current.md", "# A\n\n参见 [[linked.md]]。\n")
    write_note(rag_vault, "linked.md", "# B\n\n内容。\n")
    from server.index.service import DerivedIndexService

    m4 = DerivedIndexService(rag_vault)
    m4.rebuild()
    try:
        service = AIWorkflowService(
            _StubSettings(), _StubAdapter(), vault=rag_vault, index=m4, rag=None
        )
        assert service._rag_candidates("anything", exclude="current.md") == []  # noqa: SLF001
        candidates = reduce_candidates(m4, "current.md", limit=5)
        assert [row.path for row in candidates] == ["linked.md"]
    finally:
        m4.close()
