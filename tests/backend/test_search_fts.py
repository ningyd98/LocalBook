"""M4 FTS search matrix (PLAN-M4 §5.4/§9.1).

Strategy under test:
- English/number/hyphen queries ride the FTS5 ``unicode61`` main path
  (bm25 ranking, phrase-AND semantics);
- Chinese (any length), emoji, accented or symbol terms degrade to the M3
  substring path (unicode61 cannot tokenize CJK, trigram needs >= 3 chars —
  PLAN-M4 §5.4 measured conclusion);
- FTS returning zero rows also falls back to substring so tokenization
  edge cases cannot hide M3-parity results;
- FTS unavailability falls back to substring with no 5xx.

The DTO never changes: ``SearchResponse`` / ``SearchHit`` fields are
identical to M3.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from server.index.service import DerivedIndexService
from server.search.service import SearchService
from server.vault.errors import InvalidRequest
from server.vault.service import VaultService


def _make_vault(vault_service_factory: Callable[..., VaultService], tmp_path: Path):
    root = tmp_path / "vault"
    root.mkdir()
    service = vault_service_factory(root)
    notes = {
        "english.md": b"# English Note\nhello world from the alpha beta body\n",
        "mixed.md": (
            "# 混合笔记\n\nhello from Chinese 你好世界 and café latin 世界 你好 \n"
        ).encode(),
        "tagged.md": b"---\ntags: [work, urgent]\n---\n# Tagged note\nbody text here\n",
        "basename.md": b"# Basename\ncombo-ish unique words inside\n",
        "emoji.md": "# Emoji\nhappy \N{GRINNING FACE} face body\n".encode("utf-8"),
        "frag.md": b"# Frag\nxyzabc marker text\n",
    }
    for name, data in notes.items():
        service.create_bytes(name, data)
    index = DerivedIndexService(service)
    result = index.rebuild()
    assert result.ready is True
    assert index.fts_available is True
    return service, index, SearchService(index)


def test_english_fts_hits_and_ranking(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    _service, _index, search = _make_vault(vault_service_factory, tmp_path)
    result = search.search("hello")
    paths = [hit.path for hit in result.hits]
    assert "english.md" in paths
    assert "mixed.md" in paths
    scores = [hit.score for hit in result.hits]
    assert scores == sorted(scores, reverse=True)
    assert result.query == "hello"
    # bm25-based scores are positive after negation and deterministic
    assert all(isinstance(hit.score, float) for hit in result.hits)


def test_multi_term_english_implicit_and(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    _service, _index, search = _make_vault(vault_service_factory, tmp_path)
    result = search.search("hello alpha")
    paths = {hit.path for hit in result.hits}
    assert paths == {"english.md"}
    result2 = search.search("hello absent-term-zzz")
    assert result2.total == 0


def test_hyphenated_queries_are_fts_safe(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    """Hyphens are FTS punctuation: a hyphenated doc term is tokenised into
    adjacent tokens, so quoted-phrase queries match it without SQL errors
    (a bare ``new-term`` MATCH would be parsed as column minus-operator)."""
    service, index, search = _make_vault(vault_service_factory, tmp_path)
    service.create_bytes("dash.md", b"# Dash\nhello new-term-abc-123 tail\n")
    index.handle_event(
        __import__("server.vault.events", fromlist=["VaultEvent"]).VaultEvent.created(
            "dash.md"
        )
    )
    index.flush_events()  # P1-3: process buffered events immediately
    hits = [hit.path for hit in search.search("new-term-abc-123").hits]
    assert "dash.md" in hits


def test_chinese_two_char_query_degrades_to_substring(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    _service, _index, search = _make_vault(vault_service_factory, tmp_path)
    result = search.search("你好")
    paths = {hit.path for hit in result.hits}
    assert "mixed.md" in paths
    # degraded path keeps M3 scoring shape and pure-text snippets
    for hit in result.hits:
        assert hit.matched_terms == ["你好"]


def test_chinese_plus_english_mixed_query_substring_and(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    _service, _index, search = _make_vault(vault_service_factory, tmp_path)
    result = search.search("你好 世界")
    assert {hit.path for hit in result.hits} == {"mixed.md"}
    for hit in result.hits:
        assert set(hit.matched_terms) == {"你好", "世界"}


def test_emoji_query_degrades_to_substring(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    _service, _index, search = _make_vault(vault_service_factory, tmp_path)
    result = search.search("😀")
    paths = {hit.path for hit in result.hits}
    assert "emoji.md" in paths


def test_tag_and_basename_hits(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    _service, _index, search = _make_vault(vault_service_factory, tmp_path)
    # English tag token found through the FTS tags column
    assert "tagged.md" in {hit.path for hit in search.search("work").hits}
    # basename token found through the FTS basename column
    assert "basename.md" in {hit.path for hit in search.search("basename").hits}


def test_fts_miss_falls_back_to_substring(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    """FTS tokenises 'xyzabc' as one token, so a 'xyz' MATCH finds nothing;
    the fallback substring path still finds the note (M3 parity)."""
    _service, _index, search = _make_vault(vault_service_factory, tmp_path)
    hits = [hit.path for hit in search.search("xyz").hits]
    assert "frag.md" in hits


def test_fts_unavailable_degrades_to_substring_without_error(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
    monkeypatch,
) -> None:
    _service, index, search = _make_vault(vault_service_factory, tmp_path)
    monkeypatch.setattr(index, "_fts_available", False)
    # ASCII query: FTS skipped entirely -> substring path still finds notes
    english = search.search("hello")
    assert "english.md" in {hit.path for hit in english.hits}
    # Chinese still works the same way
    chinese = search.search("你好")
    assert "mixed.md" in {hit.path for hit in chinese.hits}


def test_search_dto_shape_and_validation(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    _service, _index, search = _make_vault(vault_service_factory, tmp_path)
    result = search.search("hello")
    assert set(result.model_dump()) == {
        "query",
        "hits",
        "total",
        "degraded",
        "skipped_notes",
        "generated_at",
    }
    assert result.total == len(result.hits)
    for query in ("", "   ", "a\u0000b", "x" * 300, "y" * 65):
        try:
            search.search(query)
            raise AssertionError(f"expected InvalidRequest for {query!r}")
        except InvalidRequest:
            pass


def test_unreadable_notes_never_appear_in_fts_hits(
    vault_fixture_copy: Path,
    vault_service_factory: Callable[..., VaultService],
    index_service_factory: Callable[..., DerivedIndexService],
) -> None:
    service = vault_service_factory(vault_fixture_copy)
    index = index_service_factory(service)
    search = SearchService(index)
    for query in ("hello", "你好"):
        hits = search.search(query).hits
        assert all("non-utf8" not in hit.path for hit in hits)
    assert index.failed_count >= 1


def test_snippet_is_plain_text_window(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    _service, _index, search = _make_vault(vault_service_factory, tmp_path)
    for query in ("hello", "你好", "😀", "xyz"):
        for hit in search.search(query).hits:
            assert "<" not in hit.snippet  # never raw HTML
            assert isinstance(hit.snippet, str)
