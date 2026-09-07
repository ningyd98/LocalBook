"""M5 GraphService projection unit tests (PLAN-M5 §9.1 rows 1–6).

Run against a rebuilt real index over the committed Vault fixture so the
rows (links/backlinks/tags incl. Chinese/Emoji/space/duplicate-basename
files) are exactly what M4 produced.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from server.graph.service import GraphService, note_id, tag_id
from server.index.service import DerivedIndexService
from server.vault.errors import PathNotFound
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


def _by_id(payload: object) -> tuple[dict[str, dict], list[dict]]:
    nodes = {item["id"]: item for item in payload["nodes"]}
    return nodes, list(payload["edges"])


def _note(payload: object, path: str) -> dict:
    nodes, _ = _by_id(payload)
    return nodes[note_id(path)]


# ---------------------------------------------------------------------------
# Global scope
# ---------------------------------------------------------------------------


def test_global_contains_all_notes_and_tags(graph: GraphService) -> None:
    payload = graph.global_graph(limit=2000).model_dump(mode="json")
    nodes, edges = _by_id(payload)
    assert payload["model"] == "note-tag-v1"
    assert payload["scope"] == "global"
    assert payload["root"] is None
    assert payload["page"]["total_nodes"] == len(nodes) == 28
    assert payload["page"]["total_edges"] == len(edges) == 25
    assert payload["page"]["truncated"] is False
    # Isolated note with a space in its path is present
    assert note_id("nested/with space.md") in nodes
    # Chinese and Emoji notes are present
    assert note_id("中文 note.md") in nodes
    assert note_id("中文与 Unicode/😀 note.md") in nodes
    # unreadable notes stay notes (not ghosts)
    assert note_id("bytes/non-utf8.md") in nodes


def test_global_tag_nodes_have_deterministic_labels(graph: GraphService) -> None:
    payload = graph.global_graph(limit=2000).model_dump(mode="json")
    nodes, _ = _by_id(payload)
    work = nodes[tag_id("工作")]
    assert work["type"] == "tag"
    assert work["tag_folded"] == "工作"
    assert work["tag"] == "工作"
    assert work["path"] is None and work["title"] is None
    alpha = nodes[tag_id("alpha")]
    assert alpha["label"] == "alpha"


def test_global_link_edge_semantics(graph: GraphService) -> None:
    payload = graph.global_graph(limit=2000).model_dump(mode="json")
    nodes, edges = _by_id(payload)
    by_raw = {edge["raw"]: edge for edge in edges if edge["raw"]}

    # resolved target keeps section/block metadata as edge attributes
    ref = by_raw["[[Ref A]]"]
    assert ref["type"] == "link"
    assert ref["source"] == note_id("notes/a.md")
    assert ref["target"] == note_id("notes/Ref A.md")
    assert ref["broken"] is False and ref["ambiguous"] is False

    # broken keeps the source edge with broken=true and NO virtual target
    cfg = by_raw["[[Cfg B]]"]
    assert cfg["broken"] is True
    assert cfg["target"] == ""
    assert cfg["source"] == note_id("notes/a.md")
    assert note_id("Cfg B") not in nodes
    assert not any(node["label"] == "Cfg B" for node in payload["nodes"])

    # ambiguous preserves the M4 candidates in deterministic order
    alpha = by_raw["[[Alpha]]"]
    assert alpha["ambiguous"] is True
    assert alpha["candidates"] == ["dup/Alpha.md", "notes/Alpha.md"]
    assert alpha["resolved_path"] == "dup/Alpha.md"
    assert alpha["target"] == note_id("dup/Alpha.md")

    # self-anchor keeps section and resolves to the source note itself
    self_heading = by_raw["[[#SelfHeading]]"]
    assert self_heading["section"] == "SelfHeading"
    assert self_heading["target"] == note_id("combo.md")
    assert self_heading["resolved_path"] == "combo.md"

    # embed -> attachment is NOT a node and creates no edge
    assert "![[image.png]]" not in by_raw
    assert note_id("attachments/image.png") not in nodes
    # web links are not in the local graph and never create remote nodes
    assert "https://example.com/page" not in by_raw
    assert not any("https" in (node.get("path") or "") for node in payload["nodes"])

    # duplicates from one source stay distinct edges (seq embedded in id)
    chinese_edges = [
        e for e in edges if e["raw"] == "[[中文 note]]" and e["source"] == note_id("combo.md")
    ]
    assert len(chinese_edges) == 1
    all_chinese_from_combo = [
        e for e in edges if e["source"] == note_id("combo.md")
        and e["target"] == note_id("中文 note.md")
    ]
    assert len(all_chinese_from_combo) == 2  # [[中文 note|显示名]] + [[中文 note]]
    assert len({e["id"] for e in all_chinese_from_combo}) == 2


def test_global_tag_edges_and_duplicate_collapse(graph: GraphService) -> None:
    payload = graph.global_graph(limit=2000).model_dump(mode="json")
    nodes, edges = _by_id(payload)
    tag_edges = [e for e in edges if e["type"] == "tag"]
    assert len(tag_edges) == 8
    a_alpha = tag_edge_between(tag_edges, "notes/a.md", "alpha")
    assert a_alpha["source"] == note_id("notes/a.md")
    assert a_alpha["target"] == tag_id("alpha")
    # duplicate frontmatter tag [alpha, beta, alpha] produced exactly one edge
    assert sum(
        1 for e in tag_edges
        if e["source"] == note_id("notes/a.md") and e["target"] == tag_id("alpha")
    ) == 1


def tag_edge_between(edges: list[dict], note_path: str, folded: str) -> dict:
    matches = [
        e for e in edges
        if e["source"] == note_id(note_path) and e["target"] == tag_id(folded)
    ]
    assert len(matches) == 1
    return matches[0]


def test_global_deterministic_repeated_requests(graph: GraphService) -> None:
    first = graph.global_graph(limit=2000).model_dump(mode="json")
    second = graph.global_graph(limit=2000).model_dump(mode="json")
    first.pop("generated_at")
    second.pop("generated_at")
    assert first == second


def test_include_broken_false_drops_only_dangling_edges(graph: GraphService) -> None:
    with_broken = graph.global_graph(limit=2000)
    without = graph.global_graph(limit=2000, include_broken=False)
    assert without.page.total_nodes == with_broken.page.total_nodes
    dangling = sum(
        1 for edge in with_broken.edges if edge.target == ""
    )
    assert without.page.total_edges == with_broken.page.total_edges - dangling
    assert all(edge.target for edge in without.edges)


# ---------------------------------------------------------------------------
# Local scope (BFS)
# ---------------------------------------------------------------------------


def test_local_depth_zero_is_root_and_its_tags(graph: GraphService) -> None:
    payload = graph.local_graph("notes/a.md", depth=0).model_dump(mode="json")
    nodes, edges = _by_id(payload)
    assert payload["scope"] == "local"
    assert payload["root"] == "notes/a.md"
    assert set(nodes) == {note_id("notes/a.md"), tag_id("alpha"), tag_id("beta")}
    tag_edges = [e for e in edges if e["type"] == "tag"]
    assert len(tag_edges) == 2
    # the root's own broken ref stays as a dangling link edge (source in scope)
    broken = [e for e in edges if e["type"] == "link" and e["broken"]]
    assert len(broken) == 1
    assert broken[0]["raw"] == "[[Cfg B]]"
    assert broken[0]["target"] == ""

    # without broken edges the depth-0 scope is root + its tags only
    without = graph.local_graph("notes/a.md", depth=0, include_broken=False)
    assert len(without.edges) == 2
    assert all(edge.type == "tag" for edge in without.edges)


def test_local_outgoing_and_incoming_directions(graph: GraphService) -> None:
    # outgoing depth 1 from notes/a.md: Ref A + ambiguous Alpha target +
    # Chinese note (embed/web never traverse)
    outgoing = graph.local_graph(
        "notes/a.md", depth=1, direction="outgoing"
    ).model_dump(mode="json")
    out_nodes, _ = _by_id(outgoing)
    assert {note_id("notes/a.md"), note_id("notes/Ref A.md"),
            note_id("dup/Alpha.md"), note_id("中文 note.md"),
            tag_id("alpha"), tag_id("beta"),
            tag_id("中文标签"), tag_id("工作")} <= set(out_nodes)
    assert note_id("combo.md") not in out_nodes  # not reached at depth 1
    assert note_id("notes/Alpha.md") not in out_nodes  # non-deterministic candidate

    both = graph.local_graph("notes/a.md", depth=2).model_dump(mode="json")
    both_nodes, _ = _by_id(both)
    assert note_id("combo.md") in both_nodes  # reached via Ref A at depth 2
    assert note_id("dup/Alpha.md") in both_nodes

    # incoming depth 1 of notes/Ref A.md: only notes/a.md points at it
    incoming = graph.local_graph(
        "notes/Ref A.md", depth=1, direction="incoming"
    ).model_dump(mode="json")
    in_nodes, _ = _by_id(incoming)
    assert note_id("notes/a.md") in in_nodes
    assert note_id("notes/Ref A.md") in in_nodes
    assert note_id("combo.md") not in in_nodes


def test_local_scope_edge_set_has_no_dangling_normal_edges(graph: GraphService) -> None:
    payload = graph.local_graph("notes/a.md", depth=1, direction="both")
    node_ids = {node.id for node in payload.nodes}
    for edge in payload.edges:
        if edge.target:
            assert edge.target in node_ids
        assert edge.source in node_ids


def test_local_missing_root_404s(graph: GraphService) -> None:
    with pytest.raises(PathNotFound):
        graph.local_graph("notes/never.md", depth=1)


def test_local_tag_filter_keeps_root_when_filter_misses(graph: GraphService) -> None:
    # notes/Alpha.md carries no tags: a filter that matches nothing keeps the
    # root note only (PLAN-M5 §10.2 recommendation).
    payload = graph.local_graph(
        "notes/Alpha.md", depth=2, tag="不存在的标签"
    ).model_dump(mode="json")
    nodes, edges = _by_id(payload)
    assert set(nodes) == {note_id("notes/Alpha.md")}
    assert edges == []

    # A matching filter narrows the scope to notes carrying the tag.
    payload = graph.local_graph("中文 note.md", depth=1, tag="工作")
    assert payload.page.total_nodes == 2
    assert {node.id for node in payload.nodes} == {
        note_id("中文 note.md"),
        tag_id("工作"),
    }


# ---------------------------------------------------------------------------
# Tag scope
# ---------------------------------------------------------------------------


def test_tag_scope_returns_bearing_notes_and_tag_node(graph: GraphService) -> None:
    payload = graph.tag_graph("工作").model_dump(mode="json")
    nodes, edges = _by_id(payload)
    assert payload["scope"] == "tag"
    assert {n["id"] for n in payload["nodes"]} == {
        note_id("frontmatter/unknown-fields.md"),
        note_id("中文 note.md"),
        tag_id("工作"),
    }
    assert len(edges) == 2
    assert all(e["type"] == "tag" for e in edges)
    assert all(e["target"] == tag_id("工作") for e in edges)
    assert nodes[tag_id("工作")]["tag_folded"] == "工作"


def test_tag_scope_is_casefolded(graph: GraphService) -> None:
    # Case-insensitive matching against *existing* folded tags: ALPHA -> alpha
    payload = graph.tag_graph("ALPHA").model_dump(mode="json")
    nodes, _ = _by_id(payload)
    assert nodes[tag_id("alpha")]["tag_folded"] == "alpha"
    assert note_id("notes/a.md") in nodes
    assert payload["page"]["total_nodes"] == 2  # notes/a.md + tag node

    # Repeated lookups are stable and casefold never fabricates a new tag.
    assert graph.tag_graph("ALPHA").model_dump(
        exclude={"generated_at"}
    ) == graph.tag_graph("alpha").model_dump(exclude={"generated_at"})


def test_tag_scope_unknown_tag_404s(graph: GraphService) -> None:
    with pytest.raises(PathNotFound):
        graph.tag_graph("完全不存在的标签")


def test_global_tag_filter_matches_tag_scope(graph: GraphService) -> None:
    global_filtered = graph.global_graph(tag="工作")
    tag_scope = graph.tag_graph("工作")
    assert global_filtered.model_dump(
        exclude={"scope", "generated_at"}
    ) == tag_scope.model_dump(exclude={"scope", "generated_at"})
    assert global_filtered.scope == "global"
    assert tag_scope.scope == "tag"


def test_global_tag_filter_with_unknown_tag_is_empty_not_404(graph: GraphService) -> None:
    payload = graph.global_graph(tag="从不存在的标签").model_dump(mode="json")
    assert payload["nodes"] == []
    assert payload["edges"] == []
    assert payload["page"]["total_nodes"] == 0
    assert payload["page"]["truncated"] is False
