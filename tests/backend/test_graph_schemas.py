"""M5 DTO/id contract tests (PLAN-M5 §5.1/§5.2/§9.1).

Locks the wire shape Python ships so ``packages/protocol`` can mirror it
byte-for-byte: literal types, ``extra="forbid"``, stable percent-encoded IDs
and deterministic link edge IDs that embed the M4 ``seq`` (duplicated links
stay distinct).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from server.graph.schemas import (
    GraphEdge,
    GraphNode,
    GraphPage,
    GraphResponse,
)
from server.graph.service import (
    link_edge_id,
    note_id,
    tag_edge_id,
    tag_id,
)
from server.index.schemas import (
    GraphLinkRow,
    GraphNoteRow,
    GraphSnapshot,
    GraphTagRow,
    GraphTotals,
)


def _response() -> GraphResponse:
    return GraphResponse(
        model="note-tag-v1",
        scope="global",
        root=None,
        nodes=[
            GraphNode(
                id=note_id("中文 note.md"),
                type="note",
                label="中文 note",
                path="中文 note.md",
                title="中文 note",
            ),
            GraphNode(id=tag_id("工作"), type="tag", label="工作", tag="工作", tag_folded="工作"),
        ],
        edges=[
            GraphEdge(
                id=tag_edge_id("中文 note.md", "工作"),
                source=note_id("中文 note.md"),
                target=tag_id("工作"),
                type="tag",
            )
        ],
        page=GraphPage(
            limit=500, offset=0, next_offset=None, total_nodes=2, total_edges=1,
            truncated=False,
        ),
        generated_at=datetime.now(UTC),
    )


def test_response_shape_and_literals() -> None:
    payload = _response().model_dump(mode="json")
    assert payload["model"] == "note-tag-v1"
    assert payload["scope"] == "global"
    assert payload["root"] is None
    assert set(payload) == {"model", "scope", "root", "nodes", "edges", "page", "generated_at"}
    assert set(payload["nodes"][0]) == {"id", "type", "label", "path", "title", "tag", "tag_folded"}
    assert set(payload["nodes"][1]) == {"id", "type", "label", "path", "title", "tag", "tag_folded"}
    assert set(payload["edges"][0]) == {
        "id", "source", "target", "type", "directed", "raw", "resolved_path",
        "section", "block", "broken", "ambiguous", "candidates", "context",
    }
    assert set(payload["page"]) == {
        "limit", "offset", "next_offset", "total_nodes", "total_edges", "truncated",
    }
    assert payload["generated_at"]  # ISO-8601 string under mode="json"


def test_extra_fields_are_forbidden() -> None:
    with pytest.raises(ValidationError):
        GraphNode(id="x", type="note", label="x", extra="nope")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        GraphResponse.model_validate(
            _response().model_dump() | {"extra": 1}
        )
    with pytest.raises(ValidationError):
        GraphNode(id="x", type="heading", label="x")  # type: ignore[arg-type]


def test_empty_target_is_legal_for_dangling_edge() -> None:
    edge = GraphEdge(
        id=link_edge_id("notes/a.md", 1),
        source=note_id("notes/a.md"),
        target="",
        type="link",
        broken=True,
        raw="[[Cfg B]]",
    )
    assert edge.target == ""
    assert edge.broken is True
    assert edge.candidates == []


def test_stable_ids_percent_encode_utf8_bytes() -> None:
    # Every UTF-8 byte is percent-encoded (uppercase hex, no safe charset)
    # so IDs are opaque, deterministic and never need decoding.
    expected = "note:" + "".join("%{:02X}".format(b) for b in b"notes/a.md")
    assert note_id("notes/a.md") == expected
    chinese = note_id("中文 note.md")
    assert chinese.startswith("note:%E4%B8%AD%E6%96%87%20")
    assert chinese == note_id("中文 note.md")  # deterministic
    emoji = note_id("中文与 Unicode/😀 note.md")
    assert "%F0%9F%98%80" in emoji  # emoji is 4 UTF-8 bytes

    # Different tags/paths always map to different node IDs.
    assert note_id("notes/a.md") != note_id("notes/b.md")
    assert tag_id("Work") != tag_id("work")
    assert tag_id("工作") == tag_id("工作")


def test_link_edge_ids_preserve_duplicates_via_seq() -> None:
    first = link_edge_id("combo.md", 4)
    second = link_edge_id("combo.md", 7)
    assert first != second
    assert first.endswith("#4")
    assert second.endswith("#7")
    assert link_edge_id("combo.md", 4) == first  # stable
    assert tag_edge_id("combo.md", "工作") != tag_edge_id("notes/a.md", "工作")
    assert tag_edge_id("combo.md", "工作") == tag_edge_id("combo.md", "工作")


def test_internal_snapshot_rows_are_frozen_dataclasses() -> None:
    note = GraphNoteRow(path="a.md", title="A", basename="a")
    tag = GraphTagRow(note_path="a.md", tag="alpha", tag_folded="alpha", seq=0)
    link = GraphLinkRow(
        source_path="a.md",
        seq=0,
        target="Ref A",
        raw="[[Ref A]]",
        kind="wikilink",
        resolved_path="notes/Ref A.md",
        broken=False,
        ambiguous=False,
        candidates=("notes/Ref A.md",),
    )
    snapshot = GraphSnapshot(notes=(note,), tags=(tag,), links=(link,))
    assert snapshot.notes[0].path == "a.md"
    assert snapshot.links[0].candidates == ("notes/Ref A.md",)
    assert isinstance(snapshot, GraphSnapshot)
    with pytest.raises(Exception):
        snapshot.notes[0].path = "other.md"  # frozen
    totals = GraphTotals(note_count=1, tag_count=1, link_count=1)
    assert totals.note_count == 1
