"""Programmatic related-note candidate reduction tests (PLAN-M6 §9.1.8 / B3)."""

from __future__ import annotations

from dataclasses import dataclass, field

from server.ai.candidates import reduce_candidates


@dataclass
class FakeRow:
    path: str
    resolved_targets: set[str] = field(default_factory=set)
    outgoing: list[object] = field(default_factory=list)
    title: str = ""


@dataclass
class FakeOutgoing:
    resolved_path: str | None = None


@dataclass
class FakeLink:
    source_path: str
    resolved_path: str | None


class FakeIndex:
    """Minimal read-only index double for candidates.py."""

    def __init__(
        self,
        rows: list[FakeRow],
        *,
        fts_hits: list[str] | None = None,
        substring_hits: list[str] | None = None,
        graph_links: list[FakeLink] | None = None,
    ) -> None:
        self._rows = rows
        self._by_path = {row.path: row for row in rows}
        self._fts_hits = list(fts_hits or [])
        self._substring_hits = list(substring_hits or [])
        self.graph_links = list(graph_links or [])
        self.fts_calls: list[str] = []
        self.substring_calls: list[list[str]] = []

    def entries(self) -> list[FakeRow]:
        return list(self._rows)

    def fts_search(self, expr: str) -> list[FakeRow]:
        self.fts_calls.append(expr)
        return [self._by_path[p] for p in self._fts_hits if p in self._by_path]

    def substring_search(self, terms: list[str]) -> list[FakeRow]:
        self.substring_calls.append(terms)
        return [self._by_path[p] for p in self._substring_hits if p in self._by_path]

    def graph_snapshot(self) -> object:
        return type("Snapshot", (), {"links": self.graph_links})()


def test_excludes_current_and_rejects_paths_outside_entries() -> None:
    rows = [FakeRow(path="me.md"), FakeRow(path="a.md")]
    index = FakeIndex(rows, fts_hits=["me.md", "a.md", "ghost.md"])
    result = reduce_candidates(index, "me.md", limit=10, query="hello")
    assert [r.path for r in result] == ["a.md"]  # current + ghost excluded


def test_fts_first_then_substring_fallback() -> None:
    rows = [FakeRow(path="a.md"), FakeRow(path="b.md")]
    index = FakeIndex(rows, substring_hits=["b.md"])
    result = reduce_candidates(index, "me.md", limit=10, query="量子 widgets")
    # CJK term is not FTS-safe → substring path only.
    assert [r.path for r in result] == ["b.md"]
    assert index.substring_calls == [["量子", "widgets"]]
    assert index.fts_calls == []

    index2 = FakeIndex(rows, fts_hits=["a.md"], substring_hits=["b.md"])
    result2 = reduce_candidates(index2, "me.md", limit=10, query="rocket science")
    assert [r.path for r in result2] == ["a.md"]  # FTS wins when it hits
    assert index2.fts_calls == ['"rocket" "science"']
    assert index2.substring_calls == []


def test_fts_empty_degrades_to_substring() -> None:
    rows = [FakeRow(path="a.md")]
    index = FakeIndex(rows, substring_hits=["a.md"])
    result = reduce_candidates(index, "me.md", limit=10, query="alpha")
    assert [r.path for r in result] == ["a.md"]
    assert len(index.fts_calls) == 1
    assert index.substring_calls == [["alpha"]]


def test_link_neighbors_merge_and_broken_web_excluded() -> None:
    rows = [
        FakeRow(path="me.md", resolved_targets={"ok.md", "broken.md"}),
        FakeRow(path="ok.md"),
        FakeRow(path="back.md"),
    ]
    index = FakeIndex(
        rows,
        graph_links=[
            FakeLink("back.md", "me.md"),          # inbound neighbor
            FakeLink("me.md", "https://example.com"),  # web target absent from index
        ],
    )
    # No query → links/graph only.
    result = reduce_candidates(index, "me.md", limit=10)
    paths = [r.path for r in result]
    assert "ok.md" in paths
    assert "back.md" in paths
    assert "broken.md" not in paths  # absent from entries (broken link)
    assert all(p != "me.md" for p in paths)


def test_dedupe_and_limit() -> None:
    rows = [
        FakeRow(path="me.md", resolved_targets={"a.md", "b.md"}),
        FakeRow(path="a.md"),
        FakeRow(path="b.md"),
    ]
    index = FakeIndex(rows, fts_hits=["a.md", "b.md"])
    result = reduce_candidates(index, "me.md", limit=1, query="x")
    assert len(result) == 1  # bounded even though 3 merge sources exist


def test_empty_query_and_no_sources_returns_empty() -> None:
    index = FakeIndex([FakeRow(path="me.md")])
    assert reduce_candidates(index, "me.md", limit=10) == []
    assert reduce_candidates(index, "me.md", limit=0, query="x") == []


def test_no_query_means_no_text_search() -> None:
    index = FakeIndex([FakeRow(path="me.md"), FakeRow(path="a.md")])
    reduce_candidates(index, "me.md", limit=10)
    assert index.fts_calls == []
    assert index.substring_calls == []
