"""M14 roadmap item ③ — link/graph weighted retrieval.

The link path answers a question the lexical and semantic paths cannot: *which
notes are connected to the notes the query already found*. These tests pin the
four signals (wikilink out-edge, backlink in-edge, same tag, two-hop graph
neighbour), the weight/determinism contract, the "never return a path that does
not exist / the current note" rule, the honest line-number behaviour, and the
degradation contract of the hybrid retriever.

Everything runs offline against a throwaway Vault (``tmp_path``): the derived
index is built from the same Markdown files the RAG index uses, never from a real
user Vault, and no test writes into the Vault or the derived database.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from server.index.service import DerivedIndexService
from server.rag.embeddings.base import MockEmbeddingProvider
from server.rag.embeddings.runner import EmbeddingRunner
from server.rag.index_service import RagIndexService
from server.rag.retrieval.base import Retriever, RetrieverUnavailable
from server.rag.retrieval.hybrid import HybridRetriever
from server.rag.retrieval.keyword import KeywordRetriever
from server.rag.retrieval.link import (
    SIGNAL_GRAPH,
    LinkRetriever,
)
from server.rag.schemas import RetrievalResult, content_hash
from server.rag.service import RagService
from tests.rag.conftest import make_index_service, write_note

QUERY = "云边协同"


# ----------------------------------------------------------------------
# Fixtures / helpers
# ----------------------------------------------------------------------


@pytest.fixture
def m4(rag_vault):
    """A real M4 derived index over the throwaway Vault (read-only for the RAG)."""
    service = DerivedIndexService(rag_vault)
    yield service
    service.close()


def _build_rag_index(rag_vault, store, provider=None) -> RagIndexService:
    provider = provider or MockEmbeddingProvider(dimension=32)
    index = make_index_service(rag_vault, store, provider=provider)
    index.rebuild()
    return index


def _link(store, m4, **kwargs) -> LinkRetriever:
    """Link retriever with a single, unambiguous lexical seed by default."""
    kwargs.setdefault("seed_top_k", 1)
    return LinkRetriever(store, index=m4, **kwargs)


def _paths(results: list[RetrievalResult]) -> list[str]:
    return [item.path for item in results]


# ----------------------------------------------------------------------
# Protocol / construction
# ----------------------------------------------------------------------


def test_link_retriever_implements_the_retriever_protocol(store, m4) -> None:
    assert isinstance(_link(store, m4), Retriever)


def test_link_retriever_without_index_returns_empty_and_reports_degradation(
    store,
) -> None:
    """No derived graph ⇒ no candidates, and the reason is recorded, no raise."""
    retriever = LinkRetriever(store, index=None)
    assert retriever.retrieve(QUERY, top_k=5) == []
    assert retriever.last_degraded == "link_unavailable"


def test_link_retriever_without_seed_returns_empty(store, m4) -> None:
    retriever = _link(store, m4)
    assert retriever.retrieve("zzz-nothing-matches-this", top_k=5) == []
    assert retriever.last_degraded == "link_no_seed"


def test_link_retriever_ignores_an_empty_query(store, m4) -> None:
    retriever = _link(store, m4)
    assert retriever.retrieve("   ", top_k=5) == []
    assert retriever.last_degraded is None


# ----------------------------------------------------------------------
# Static status: "" / "enabled" / degradation reason (never from last_degraded)
# ----------------------------------------------------------------------


def test_link_retrieval_status_is_empty_when_the_path_is_not_wired(
    rag_vault, store
) -> None:
    hybrid = HybridRetriever(keyword=KeywordRetriever(store))
    assert hybrid.link_enabled is False
    assert hybrid.link_retrieval == ""


def test_link_retrieval_status_reports_an_unreachable_graph_before_any_query(
    store,
) -> None:
    """A fresh process has not searched yet: the status must not need a query.

    The link path is wired (web-eng's factory does exactly this when the switch
    is on but the derived index is missing), so "enabled" here would be the
    "pretend it is on" bug. ``last_degraded`` is still ``None`` at this point,
    which is what proves the status is computed directly.
    """
    link = LinkRetriever(store, index=None)
    hybrid = HybridRetriever(keyword=KeywordRetriever(store), link=link)
    assert link.last_degraded is None  # never ran
    assert hybrid.link_enabled is True
    assert hybrid.link_retrieval != "enabled"
    assert hybrid.link_retrieval == "link_unavailable"
    assert link.link_retrieval == "link_unavailable"


def test_link_retrieval_status_follows_the_index_build_state(store) -> None:
    """A derived index that reports itself unavailable is not "enabled"."""

    class UnavailableIndex:
        build_state = "unavailable"

        def fts_search(self, match_expr):  # pragma: no cover - never reached
            return []

        def graph_snapshot(self):  # pragma: no cover - never reached
            raise AssertionError("an unavailable index must not be read")

        def entry(self, path):  # pragma: no cover - never reached
            return None

    link = LinkRetriever(store, index=UnavailableIndex())
    assert link.link_retrieval == "link_unavailable"
    hybrid = HybridRetriever(keyword=KeywordRetriever(store), link=link)
    assert hybrid.link_retrieval == "link_unavailable"


def test_link_retrieval_status_is_enabled_when_the_graph_is_reachable(
    rag_vault, store, m4
) -> None:
    write_note(rag_vault, "a.md", f"# {QUERY}\n\n参见 [[b.md]]。\n")
    write_note(rag_vault, "b.md", "# B\n\n园艺。\n")
    m4.rebuild()

    link = _link(store, m4)
    hybrid = HybridRetriever(keyword=KeywordRetriever(store), link=link)
    assert link.link_retrieval == "enabled"
    assert hybrid.link_retrieval == "enabled"
    assert hybrid.link_enabled is True


def test_per_query_degradation_does_not_change_the_static_status(
    rag_vault, store, m4
) -> None:
    """"link_no_seed" is a per-query fact; reachability is a static one."""
    write_note(rag_vault, "a.md", f"# {QUERY}\n\n机械臂。\n")
    m4.rebuild()
    # No RAG chunk index: the query has no lexical anchor at all, so the link path
    # reports a *per-query* "link_no_seed" while its graph stays reachable.

    hybrid = HybridRetriever(keyword=KeywordRetriever(store), link=_link(store, m4))
    assert hybrid.link_retrieval == "enabled"
    outcome = hybrid.search("zzz-nothing-matches-this")
    assert "link_no_seed" in outcome.stats.degraded
    assert hybrid.link_retrieval == "enabled"


def test_link_retrieval_status_for_a_retriever_without_a_probe(rag_vault, store) -> None:
    """A wired retriever that cannot describe itself is reported as wired."""
    hybrid = HybridRetriever(
        keyword=KeywordRetriever(store), link=_FixedLink([_hit("b.md")])
    )
    assert hybrid.link_retrieval == "enabled"


def test_link_retriever_handles_a_graph_read_failure(rag_vault, store) -> None:
    """A failed graph read is a *degradation* (the caller records it), not a crash."""
    from server.index.errors import IndexUnavailable

    class BrokenGraph:
        """Seeds still come from the chunks; only the graph read fails."""

        def fts_search(self, match_expr):
            return []

        def graph_snapshot(self):
            raise IndexUnavailable()

        def entry(self, path):  # pragma: no cover - never reached
            return None

    write_note(rag_vault, "a.md", f"# {QUERY}\n\n机械臂。\n")
    _build_rag_index(rag_vault, store)

    retriever = LinkRetriever(store, index=BrokenGraph())
    with pytest.raises(RetrieverUnavailable):
        retriever.retrieve(QUERY, top_k=5)


# ----------------------------------------------------------------------
# The four signals
# ----------------------------------------------------------------------


def test_wikilink_out_edge_is_returned(rag_vault, store, m4) -> None:
    write_note(rag_vault, "a.md", f"# {QUERY}\n\n参见 [[b.md]]。\n")
    write_note(rag_vault, "b.md", "# 园艺工具\n\n番茄与黄瓜的种植方法。\n")
    m4.rebuild()
    _build_rag_index(rag_vault, store)

    results = _link(store, m4).retrieve(QUERY, top_k=5)
    assert _paths(results) == ["b.md"]
    assert results[0].source == "link"
    assert results[0].link_rank == 1
    # Signals are inspectable for debugging, and no other signal fired.
    assert results[0].path == "b.md"


def test_backlink_in_edge_is_returned(rag_vault, store, m4) -> None:
    write_note(rag_vault, "target.md", f"# {QUERY}\n\n机械臂在边缘节点重规划。\n")
    write_note(rag_vault, "source.md", "# 引用笔记\n\n详见 [[target.md]]。\n")
    m4.rebuild()
    _build_rag_index(rag_vault, store)

    retriever = _link(store, m4)
    results = retriever.retrieve(QUERY, top_k=5)
    assert _paths(results) == ["source.md"]
    assert retriever.last_signals["source.md"]["backlink"] == 1


def test_same_tag_neighbour_is_returned(rag_vault, store, m4) -> None:
    write_note(
        rag_vault,
        "a.md",
        f"---\ntags: [研究]\n---\n\n# {QUERY}\n\n机械臂任务分配。\n",
    )
    write_note(
        rag_vault,
        "tagged.md",
        "---\ntags: [研究]\n---\n\n# 实验记录\n\n番茄与堆肥。\n",
    )
    m4.rebuild()
    _build_rag_index(rag_vault, store)

    retriever = _link(store, m4)
    results = retriever.retrieve(QUERY, top_k=5)
    assert _paths(results) == ["tagged.md"]
    assert retriever.last_signals["tagged.md"]["tag"] == 1


def test_two_hop_graph_neighbour_is_returned_and_labelled(rag_vault, store, m4) -> None:
    write_note(rag_vault, "a.md", f"# {QUERY}\n\n参见 [[b.md]]。\n")
    write_note(rag_vault, "b.md", "# 中间节点\n\n参见 [[c.md]]。\n")
    write_note(rag_vault, "c.md", "# 远处节点\n\n堆肥与土壤。\n")
    m4.rebuild()
    _build_rag_index(rag_vault, store)

    retriever = _link(store, m4)
    results = retriever.retrieve(QUERY, top_k=5)
    by_path = {item.path: item for item in results}
    assert set(by_path) == {"b.md", "c.md"}
    # One hop is a wikilink neighbour; two hops is the graph signal only, so the
    # result says "graph" instead of pretending it was a direct link.
    assert by_path["b.md"].source == "link"
    assert by_path["c.md"].source == SIGNAL_GRAPH
    assert retriever.last_signals["c.md"]["graph"] == 1


def test_link_ranks_are_contiguous_and_reflect_the_ordered_list(
    rag_vault, store, m4
) -> None:
    write_note(rag_vault, "a.md", f"# {QUERY}\n\n[[b.md]] [[c.md]] [[d.md]]\n")
    for path in ("b.md", "c.md", "d.md"):
        write_note(rag_vault, path, f"# {path}\n\n园艺与堆肥。\n")
    m4.rebuild()
    _build_rag_index(rag_vault, store)

    results = _link(store, m4).retrieve(QUERY, top_k=5)
    assert [item.link_rank for item in results] == [1, 2, 3]
    assert _paths(results) == sorted(_paths(results))


# ----------------------------------------------------------------------
# Weights, determinism, limits
# ----------------------------------------------------------------------


def test_weights_decide_the_order(rag_vault, store, m4) -> None:
    write_note(
        rag_vault,
        "a.md",
        f"---\ntags: [研究]\n---\n\n# {QUERY}\n\n[[linked.md]]\n",
    )
    write_note(rag_vault, "linked.md", "# 被引用\n\n园艺与堆肥。\n")
    write_note(rag_vault, "tagged.md", "---\ntags: [研究]\n---\n\n# 实验\n\n堆肥。\n")
    m4.rebuild()
    _build_rag_index(rag_vault, store)

    default = _link(store, m4).retrieve(QUERY, top_k=5)
    assert _paths(default) == ["linked.md", "tagged.md"]

    # Raising the tag weight above the wikilink weight flips the order: the
    # ordering is a weighted sum, not a fixed signal priority.
    flipped = _link(store, m4, tag_weight=2.0, wikilink_weight=1.0).retrieve(
        QUERY, top_k=5
    )
    assert _paths(flipped) == ["tagged.md", "linked.md"]


def test_zero_weight_disables_a_signal(rag_vault, store, m4) -> None:
    write_note(rag_vault, "a.md", f"# {QUERY}\n\n参见 [[b.md]]。\n")
    write_note(rag_vault, "b.md", "# 邻居\n\n园艺。\n")
    m4.rebuild()
    _build_rag_index(rag_vault, store)

    assert _link(store, m4, wikilink_weight=0.0).retrieve(QUERY, top_k=5) == []


def test_order_is_deterministic_across_runs_and_instances(
    rag_vault, store, m4
) -> None:
    """A score TIE must be broken by path, never by insertion order.

    The fixture makes the two forces disagree on purpose: ``seed.md`` links
    ``[[zzz.md]]`` **before** ``[[aaa.md]]``, so the graph yields the candidates
    in the reverse of path order. Both targets get exactly one wikilink edge from
    the same seed, which is asserted below via ``last_signals`` — so the weighted
    sums are equal and the order can only come from ``link.py``'s sort key
    ``(-score, path)``.

    An insertion-ordered fixture (``[[b.md]] [[c.md]]``, as an earlier revision
    of this test used) cannot protect that property: insertion order equals path
    order there, so Python's stable sort produces the expected result even with
    the path tie-break removed. Deleting ``item[1]`` from the sort key must make
    THIS test fail.
    """
    write_note(rag_vault, "seed.md", f"# {QUERY}\n\n[[zzz.md]] [[aaa.md]]\n")
    write_note(rag_vault, "zzz.md", "# Z\n\n园艺。\n")
    write_note(rag_vault, "aaa.md", "# A\n\n堆肥。\n")
    m4.rebuild()
    _build_rag_index(rag_vault, store)

    retriever = _link(store, m4)
    first = _paths(retriever.retrieve(QUERY, top_k=10))
    second = _paths(_link(store, m4).retrieve(QUERY, top_k=10))
    third = _paths(
        LinkRetriever(store, index=m4, seed_top_k=1).retrieve(QUERY, top_k=10)
    )

    # A genuine tie: one wikilink edge each, same weight, no other signal.
    signals = retriever.last_signals
    assert set(signals) == {"aaa.md", "zzz.md"}, signals
    assert signals["aaa.md"] == signals["zzz.md"], signals
    assert signals["aaa.md"]["wikilink"] == 1, signals
    # ... so path order is the only admissible order, on every run and instance.
    assert first == second == third == ["aaa.md", "zzz.md"]


def test_top_k_limits_the_candidate_list(rag_vault, store, m4) -> None:
    write_note(rag_vault, "a.md", f"# {QUERY}\n\n[[b.md]] [[c.md]] [[d.md]]\n")
    for path in ("b.md", "c.md", "d.md"):
        write_note(rag_vault, path, f"# {path}\n\n园艺。\n")
    m4.rebuild()
    _build_rag_index(rag_vault, store)

    assert len(_link(store, m4).retrieve(QUERY, top_k=1)) == 1
    assert len(_link(store, m4).retrieve(QUERY, top_k=0)) == 0
    # The configured default is used when the caller does not pass one.
    assert len(_link(store, m4, top_k=2).retrieve(QUERY)) == 2


def test_allowed_paths_restricts_candidates(rag_vault, store, m4) -> None:
    write_note(rag_vault, "a.md", f"# {QUERY}\n\n[[b.md]] [[c.md]]\n")
    write_note(rag_vault, "b.md", "# B\n\n园艺。\n")
    write_note(rag_vault, "c.md", "# C\n\n堆肥。\n")
    m4.rebuild()
    _build_rag_index(rag_vault, store)

    results = _link(store, m4).retrieve(QUERY, top_k=5, allowed_paths=["c.md"])
    assert _paths(results) == ["c.md"]


def test_anchors_are_never_returned_as_expansions(rag_vault, store, m4) -> None:
    """The anchor note came from the query itself: it is not an expansion."""
    write_note(rag_vault, "a.md", f"# {QUERY}\n\n参见 [[b.md]]。\n")
    write_note(rag_vault, "b.md", "# B\n\n参见 [[a.md]]。\n")
    m4.rebuild()
    _build_rag_index(rag_vault, store)

    results = _link(store, m4).retrieve(QUERY, top_k=5)
    assert _paths(results) == ["b.md"]


def test_max_tag_neighbours_caps_a_popular_tag(rag_vault, store, m4) -> None:
    write_note(rag_vault, "a.md", f"---\ntags: [研究]\n---\n\n# {QUERY}\n\n机械臂。\n")
    for path in ("t1.md", "t2.md", "t3.md"):
        write_note(rag_vault, path, f"---\ntags: [研究]\n---\n\n# {path}\n\n堆肥。\n")
    m4.rebuild()
    _build_rag_index(rag_vault, store)

    assert len(_link(store, m4).retrieve(QUERY, top_k=10)) == 3
    capped = _paths(_link(store, m4, max_tag_neighbours=1).retrieve(QUERY, top_k=10))
    assert len(capped) == 1
    # The cap is applied deterministically (derived row order), never randomly.
    assert capped == _paths(_link(store, m4, max_tag_neighbours=1).retrieve(QUERY, top_k=10))


# ----------------------------------------------------------------------
# Boundaries: current note, unknown paths
# ----------------------------------------------------------------------


def test_excluded_paths_are_never_returned(rag_vault, store, m4) -> None:
    write_note(rag_vault, "a.md", f"# {QUERY}\n\n[[current.md]] [[other.md]]\n")
    write_note(rag_vault, "current.md", "# 当前笔记\n\n园艺。\n")
    write_note(rag_vault, "other.md", "# 其他\n\n堆肥。\n")
    m4.rebuild()
    _build_rag_index(rag_vault, store)

    results = _link(store, m4, exclude_paths=["current.md"]).retrieve(QUERY, top_k=5)
    assert _paths(results) == ["other.md"]


def test_unresolved_wikilinks_never_surface(rag_vault, store, m4) -> None:
    write_note(rag_vault, "a.md", f"# {QUERY}\n\n参见 [[missing.md]]。\n")
    write_note(rag_vault, "b.md", "# 存在\n\n园艺。\n")
    m4.rebuild()
    _build_rag_index(rag_vault, store)

    results = _link(store, m4).retrieve(QUERY, top_k=5)
    assert results == []
    assert all(item.path != "missing.md" for item in results)


def test_link_retriever_is_read_only(rag_vault, store, m4) -> None:
    """A retrieve run must not write a single derived row."""
    write_note(rag_vault, "a.md", f"---\ntags: [研究]\n---\n\n# {QUERY}\n\n[[b.md]]\n")
    write_note(rag_vault, "b.md", "---\ntags: [研究]\n---\n\n# B\n\n园艺。\n")
    m4.rebuild()
    _build_rag_index(rag_vault, store)

    db_path = Path(store.db_path)
    before = _table_counts(db_path)
    results = _link(store, m4).retrieve(QUERY, top_k=5)
    assert results
    after = _table_counts(db_path)
    assert after == before


def _table_counts(db_path: Path) -> dict[str, int]:
    """Row counts of every derived table the RAG/link path reads."""
    tables = ("notes", "tags", "links", "backlinks", "rag_chunks", "rag_embeddings")
    counts: dict[str, int] = {}
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        for table in tables:
            try:
                counts[table] = int(
                    connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                )
            except sqlite3.Error:
                counts[table] = -1
    finally:
        connection.close()
    return counts


# ----------------------------------------------------------------------
# Chunk-level vs document-level hits (no invented line ranges)
# ----------------------------------------------------------------------


def test_hits_name_a_real_chunk_with_real_lines(rag_vault, store, m4) -> None:
    write_note(rag_vault, "a.md", f"# {QUERY}\n\n参见 [[b.md]]。\n")
    write_note(rag_vault, "b.md", "# 邻居笔记\n\n## 细节\n\n园艺与堆肥。\n")
    m4.rebuild()
    _build_rag_index(rag_vault, store)

    results = _link(store, m4).retrieve(QUERY, top_k=5)
    assert len(results) == 1
    hit = results[0]
    assert hit.document_level is False
    assert hit.chunk_id and not hit.chunk_id.startswith("doc::")
    assert hit.content_hash
    assert 1 <= hit.start_line <= hit.end_line
    # The chunk really is part of the file it claims (and the note's own text).
    assert "园艺" in hit.content


def test_document_level_hit_is_flagged_and_invents_no_range(
    rag_vault, store, m4
) -> None:
    """When the RAG index has no chunk for a neighbour, say *document*, not lines."""
    write_note(rag_vault, "a.md", "# gardening notes\n\nSee [[b.md]].\n")
    write_note(rag_vault, "b.md", "# 邻居笔记\n\n园艺与堆肥。\n")
    m4.rebuild()
    # Deliberately no RAG index: the lexical seed still resolves through the M4
    # document FTS, but no chunk row exists for the neighbour.
    assert store.chunk_count() == 0

    results = _link(store, m4).retrieve("gardening", top_k=5)
    assert _paths(results) == ["b.md"]
    hit = results[0]
    assert hit.document_level is True
    assert hit.chunk_id == "doc::b.md"
    # No line-accurate span is known, so the defaults are kept instead of a
    # fabricated range being reported.
    assert (hit.start_line, hit.end_line) == (1, 1)
    assert hit.content_hash
    assert "园艺" in hit.content


def test_document_level_content_respects_the_char_limit(rag_vault, store, m4) -> None:
    write_note(rag_vault, "a.md", "# gardening notes\n\nSee [[b.md]].\n")
    write_note(rag_vault, "b.md", "# 邻居笔记\n\n园艺与堆肥。" + "很长的一段正文。" * 40)
    m4.rebuild()

    hit = _link(store, m4, document_char_limit=20).retrieve("gardening", top_k=5)[0]
    entry = m4.entry("b.md")
    assert entry is not None
    assert hit.document_level is True
    # The excerpt is the document's own bytes (truncated), and its hash is the
    # hash of exactly what was returned — never of something else.
    assert hit.content == entry.text[:20]
    assert hit.content_hash == content_hash(hit.content)


# ----------------------------------------------------------------------
# Hybrid integration: additive third path + degradation
# ----------------------------------------------------------------------


class _BrokenLink:
    name = "link"

    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.last_degraded = None

    def retrieve(self, query, *, top_k=30, query_vector=None, allowed_paths=None):
        raise self._exc


class _FixedLink:
    """Returns a canned list; lets a test pin the fusion policy exactly."""

    name = "link"

    def __init__(self, results: list[RetrievalResult]) -> None:
        self._results = results
        self.last_degraded = None

    def retrieve(self, query, *, top_k=30, query_vector=None, allowed_paths=None):
        return list(self._results)


def _hit(path: str, *, rank: int = 1, chunk_id: str | None = None) -> RetrievalResult:
    return RetrievalResult(
        chunk_id=chunk_id or f"{path}::0",
        path=path,
        heading="H",
        heading_path="H",
        content="内容",
        link_rank=rank,
        start_line=1,
        end_line=2,
        source="link",
        content_hash=f"hash::{path}",
    )


def test_hybrid_without_link_behaves_exactly_as_before(rag_vault, store) -> None:
    write_note(rag_vault, "a.md", f"# {QUERY}\n\n机械臂重规划。\n")
    _build_rag_index(rag_vault, store)

    plain = HybridRetriever(keyword=KeywordRetriever(store))
    explicit_none = HybridRetriever(keyword=KeywordRetriever(store), link=None)
    first = plain.search(QUERY, include_debug=True)
    second = explicit_none.search(QUERY, include_debug=True)
    assert [item.chunk_id for item in first.results] == [
        item.chunk_id for item in second.results
    ]
    assert first.stats.degraded == second.stats.degraded
    assert first.stats.link_candidates == 0 == second.stats.link_candidates
    assert not any(marker.startswith("link") for marker in first.stats.degraded)
    # The debug payload of the pre-③ stack stays untouched.
    assert "link_ranked" not in (first.stats.debug or {})


def test_hybrid_adds_link_candidates_without_reordering_direct_hits(
    rag_vault, store, m4
) -> None:
    write_note(rag_vault, "a.md", f"# {QUERY}\n\n参见 [[b.md]]。\n")
    write_note(rag_vault, "b.md", "# 邻居\n\n园艺与堆肥。\n")
    m4.rebuild()
    _build_rag_index(rag_vault, store)

    keyword = KeywordRetriever(store)
    direct = keyword.retrieve(QUERY, top_k=1)
    assert direct and direct[0].path == "a.md"
    hybrid = HybridRetriever(
        keyword=keyword, link=_link(store, m4), fts_top_k=1, link_top_k=5
    )
    outcome = hybrid.search(QUERY)
    paths = _paths(outcome.results)
    assert "b.md" in paths  # the link path found a note the pool did not have
    assert outcome.stats.link_candidates == 1
    assert paths.index("a.md") < paths.index("b.md")


def test_hybrid_add_only_policy_filters_documents_the_direct_paths_found(
    rag_vault, store, m4
) -> None:
    write_note(rag_vault, "a.md", f"# {QUERY}\n\n机械臂。\n")
    _build_rag_index(rag_vault, store)

    duplicate = _hit("a.md")  # same document the keyword path already returned
    keyword = KeywordRetriever(store)
    additive = HybridRetriever(keyword=keyword, link=_FixedLink([duplicate]))
    assert additive.search(QUERY).stats.link_candidates == 0

    full = HybridRetriever(
        keyword=keyword, link=_FixedLink([duplicate]), link_add_only=False
    )
    outcome = full.search(QUERY, include_debug=True)
    assert outcome.stats.link_candidates == 1
    assert outcome.stats.debug is not None
    assert outcome.stats.debug["link_candidates_raw"] == 1
    assert any(item.link_rank == 1 for item in outcome.results)


def test_hybrid_records_link_degradation_and_still_answers(
    rag_vault, store, m4
) -> None:
    write_note(rag_vault, "a.md", f"# {QUERY}\n\n机械臂重规划。\n")
    _build_rag_index(rag_vault, store)

    unavailable = HybridRetriever(
        keyword=KeywordRetriever(store), link=_BrokenLink(RetrieverUnavailable("down"))
    )
    outcome = unavailable.search(QUERY)
    assert outcome.results
    assert "link_unavailable" in outcome.stats.degraded

    broken = HybridRetriever(
        keyword=KeywordRetriever(store), link=_BrokenLink(RuntimeError("boom"))
    )
    outcome = broken.search(QUERY)
    assert outcome.results
    assert "link_failed" in outcome.stats.degraded

    # A real retriever without a graph degrades through the same channel.
    degraded = HybridRetriever(
        keyword=KeywordRetriever(store), link=LinkRetriever(store, index=None)
    )
    outcome = degraded.search(QUERY)
    assert outcome.results
    assert "link_unavailable" in outcome.stats.degraded


def test_hybrid_reports_no_seed_as_a_reason_not_a_failure(rag_vault, store, m4) -> None:
    write_note(rag_vault, "a.md", f"# {QUERY}\n\n机械臂。\n")
    m4.rebuild()
    # No RAG chunk index: nothing can anchor the query, so no seeds exist at all.
    hybrid = HybridRetriever(keyword=KeywordRetriever(store), link=_link(store, m4))
    outcome = hybrid.search("zzz-nothing-matches-this")
    assert "link_no_seed" in outcome.stats.degraded
    assert outcome.stats.link_candidates == 0


def test_rag_disabled_chain_never_touches_the_link_path(rag_vault, store) -> None:
    """RAG off is decided before retrieval: the link path is not even called."""

    class _ExplodingLink:
        name = "link"

        def retrieve(self, query, *, top_k=30, query_vector=None, allowed_paths=None):
            raise AssertionError("RAG is disabled: the link path must not run")

    write_note(rag_vault, "a.md", f"# {QUERY}\n\n机械臂。\n")
    index = RagIndexService(
        rag_vault,
        store,
        embedding_provider=MockEmbeddingProvider(dimension=32),
        embedding_runner=EmbeddingRunner(MockEmbeddingProvider(dimension=32)),
        enabled=False,
    )
    retriever = HybridRetriever(
        keyword=KeywordRetriever(store), link=_ExplodingLink()
    )
    service = RagService(index=index, retriever=retriever)
    response = asyncio.run(service.query(QUERY))
    assert "rag_disabled" in response.degraded
    assert response.sources == []
