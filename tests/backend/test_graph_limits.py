"""M5 limits / truncation / determinism matrix (PLAN-M5 §9.1 rows 7–8/11).

Node pages are stable-offset slices of the sorted candidate list; a
truncated page only carries edges whose endpoints are on the page; the edge
list of one response is hard-capped (``max_edges``) with ``truncated=true``;
repeated identical requests are byte-identical.  Includes the optional perf
smoke (opt-in, no CI threshold).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest

from server.config import GraphSettings
from server.graph.service import GraphService
from server.index.service import DerivedIndexService
from server.vault.errors import InvalidRequest
from server.vault.service import VaultService


@pytest.fixture
def graph(
    vault_fixture_copy: Path,
    vault_service_factory: Callable[..., VaultService],
    index_service_factory: Callable[..., DerivedIndexService],
) -> GraphService:
    service = vault_service_factory(vault_fixture_copy)
    index = index_service_factory(service)
    return GraphService(index)


def test_page_slices_cover_every_node_exactly_once(graph: GraphService) -> None:
    total = graph.global_graph(limit=2000).page.total_nodes
    step = 7
    pages = [
        graph.global_graph(limit=step, offset=offset).model_dump(mode="json")
        for offset in range(0, total, step)
    ]
    seen: list[str] = []
    for index, page in enumerate(pages):
        if index < len(pages) - 1:
            assert page["page"]["truncated"] is True
            assert page["page"]["next_offset"] == (index + 1) * step
        else:
            assert page["page"]["truncated"] is False
            assert page["page"]["next_offset"] is None
        seen.extend(node["id"] for node in page["nodes"])
    assert len(seen) == total
    assert len(set(seen)) == total


def test_truncated_pages_never_reference_offpage_nodes(graph: GraphService) -> None:
    page = graph.global_graph(limit=5, offset=0)
    page_ids = {node.id for node in page.nodes}
    assert page.page.truncated is True
    for edge in page.edges:
        assert edge.source in page_ids
        if edge.target:
            assert edge.target in page_ids
        else:
            assert edge.broken or edge.ambiguous  # dangling edge types only


def test_full_page_keeps_all_scope_edges(graph: GraphService) -> None:
    payload = graph.global_graph(limit=2000)
    assert payload.page.truncated is False
    assert len(payload.edges) == payload.page.total_edges == 25
    # every node endpoint exists on the page
    ids = {node.id for node in payload.nodes}
    for edge in payload.edges:
        assert edge.source in ids
        if edge.target:
            assert edge.target in ids


def test_edge_cap_marks_truncated(graph: GraphService) -> None:
    capped = GraphService(
        graph._index, settings=GraphSettings(max_edges=3)
    ).global_graph(limit=2000)
    assert capped.page.total_edges == 25
    assert len(capped.edges) == 3
    assert capped.page.truncated is True
    assert capped.edges == sorted(
        capped.edges, key=lambda e: (e.type, e.source, e.target, e.id)
    )


def test_limit_bounds_raise_invalid_request(graph: GraphService) -> None:
    with pytest.raises(InvalidRequest):
        graph.global_graph(limit=2001)
    with pytest.raises(InvalidRequest):
        graph.global_graph(limit=0)
    with pytest.raises(InvalidRequest):
        graph.global_graph(offset=-1)
    with pytest.raises(InvalidRequest):
        graph.local_graph("notes/a.md", depth=4)
    with pytest.raises(InvalidRequest):
        graph.local_graph("notes/a.md", depth=-1)
    with pytest.raises(InvalidRequest):
        graph.local_graph("notes/a.md", direction="up")


def test_control_and_overlong_inputs_raise_invalid_request(graph: GraphService) -> None:
    with pytest.raises(InvalidRequest):
        graph.local_graph("bad\x00path.md")
    with pytest.raises(InvalidRequest):
        graph.tag_graph("\x01")
    with pytest.raises(InvalidRequest):
        graph.global_graph(tag="a" * 5000)
    with pytest.raises(InvalidRequest):
        graph.local_graph("x" * 5000)


def test_offset_past_end_returns_empty_untouched_totals(graph: GraphService) -> None:
    payload = graph.global_graph(limit=50, offset=10_000)
    assert payload.nodes == []
    assert payload.edges == []
    assert payload.page.total_nodes == 28
    assert payload.page.next_offset is None
    assert payload.page.truncated is False


def test_repeated_requests_are_byte_identical(graph: GraphService) -> None:
    first = graph.local_graph("notes/a.md", depth=2, direction="both")
    second = graph.local_graph("notes/a.md", depth=2, direction="both")
    # generated_at is the only per-call field; everything else is byte-identical.
    first_json = first.model_dump_json(exclude={"generated_at"})
    second_json = second.model_dump_json(exclude={"generated_at"})
    assert first_json == second_json


# ---------------------------------------------------------------------------
# Optional perf smoke (opt-in, no threshold): query-time projection evidence.
# ---------------------------------------------------------------------------


@pytest.mark.perf
@pytest.mark.skipif(
    os.environ.get("LOCALNOTE_RUN_PERF") != "1",
    reason="performance benchmark is opt-in: set LOCALNOTE_RUN_PERF=1",
)
def test_graph_projection_perf_smoke(
    tmp_path: Path,
    vault_service_factory: Callable[..., VaultService],
) -> None:
    """Build a generated vault and time one global projection (reference only).

    Recorded on the dev machine (1500 notes, M4 index):
    rebuild ≈ 1.1 s; one global projection ≈ 0.1 s for 1510 nodes / 4095
    edges — query-time computation stays comfortably interactive for the
    single-user local scenario, so no materialised graph view is needed.
    """
    from perf.generate import generate_vault

    count = int(os.environ.get("LOCALNOTE_PERF_GRAPH_NOTES", "1500"))
    root = tmp_path / "graph-perf-vault"
    generate_vault(root, count=count, seed=20260906)
    service = vault_service_factory(root)
    index = DerivedIndexService(service)
    index.rebuild()
    graph = GraphService(index)

    import time

    started = time.perf_counter()
    response = graph.global_graph(limit=2000)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    # Shown with `-s` for the dev report; no CI threshold is enforced.
    print(
        f"\ngraph perf: notes={count} nodes={response.page.total_nodes} "
        f"edges={response.page.total_edges} global_ms={elapsed_ms:.1f}"
    )
    index.close()
