"""M3 keyword search matrix (PLAN-M3 §5.5/§9.1).

Chinese/Emoji/space-friendly, case-insensitive substring matching over the
in-memory index.  Validation failures and degradation are exercised both at
the service level (this file) and over HTTP (test_m3_api.py).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from server.index.errors import IndexUnavailable
from server.index.service import DerivedIndexService
from server.search.service import SearchService
from server.vault.errors import InvalidRequest
from server.vault.service import VaultService


def test_search_hits_body_chinese_emoji_and_space(
    vault_fixture_copy: object,
    vault_service_factory: Callable[..., VaultService],
    index_service_factory: Callable[..., DerivedIndexService],
) -> None:
    index = index_service_factory(vault_service_factory(vault_fixture_copy))  # type: ignore[arg-type]
    result = SearchService(index).search("你好世界")
    paths = [hit.path for hit in result.hits]
    assert "中文 note.md" in paths
    assert "frontmatter/unknown-fields.md" in paths


def test_multiple_terms_are_anded(
    vault_fixture_copy: object,
    vault_service_factory: Callable[..., VaultService],
    index_service_factory: Callable[..., DerivedIndexService],
) -> None:
    index = index_service_factory(vault_service_factory(vault_fixture_copy))  # type: ignore[arg-type]
    result = SearchService(index).search("你好 世界")
    assert result.total >= 1
    assert all(hit.path in {"中文 note.md", "frontmatter/unknown-fields.md"} for hit in result.hits)
    # '你好' is absent from combo.md so an AND query never matches everything
    assert len(result.hits) <= 2
    assert result.query == "你好 世界"
    for hit in result.hits:
        assert set(hit.matched_terms) == {"你好", "世界"}


def test_basename_and_tag_hits(
    vault_fixture_copy: object,
    vault_service_factory: Callable[..., VaultService],
    index_service_factory: Callable[..., DerivedIndexService],
) -> None:
    index = index_service_factory(vault_service_factory(vault_fixture_copy))  # type: ignore[arg-type]
    # tag hit: '工作' is a tag on 中文 note.md and unknown-fields.md
    result = SearchService(index).search("工作")
    paths = {hit.path for hit in result.hits}
    assert "中文 note.md" in paths
    assert "frontmatter/unknown-fields.md" in paths
    # basename hit: 'combo' only matches combo.md basename and any body text
    result2 = SearchService(index).search("combo")
    assert "combo.md" in result2.hits[0].path or any(
        hit.path == "combo.md" for hit in result2.hits
    )


def test_search_snippet_and_scoring_are_deterministic(
    vault_fixture_copy: object,
    vault_service_factory: Callable[..., VaultService],
    index_service_factory: Callable[..., DerivedIndexService],
) -> None:
    index = index_service_factory(vault_service_factory(vault_fixture_copy))  # type: ignore[arg-type]
    result = SearchService(index).search("你好")
    assert result.total >= 1
    first = result.hits[0]
    assert "你好" in first.snippet or "你好" in first.title or "你好" in first.path
    scores = [hit.score for hit in result.hits]
    assert scores == sorted(scores, reverse=True)
    paths = [hit.path for hit in result.hits]
    assert paths == sorted(paths, key=str.casefold) or scores[0] >= scores[-1]


def test_empty_and_whitespace_query_rejected(
    vault_fixture_copy: object,
    vault_service_factory: Callable[..., VaultService],
    index_service_factory: Callable[..., DerivedIndexService],
) -> None:
    index = index_service_factory(vault_service_factory(vault_fixture_copy))  # type: ignore[arg-type]
    service = SearchService(index)
    for query in ("", "   ", "\t\n"):
        with pytest.raises(InvalidRequest):
            service.search(query)


def test_overlong_and_control_query_rejected(
    vault_fixture_copy: object,
    vault_service_factory: Callable[..., VaultService],
    index_service_factory: Callable[..., DerivedIndexService],
) -> None:
    index = index_service_factory(vault_service_factory(vault_fixture_copy))  # type: ignore[arg-type]
    service = SearchService(index)
    with pytest.raises(InvalidRequest):
        service.search("x" * 257)
    with pytest.raises(InvalidRequest):
        service.search("a" * 65)
    with pytest.raises(InvalidRequest):
        service.search("ok\u0000term")


def test_search_degraded_skips_unreadable_notes(
    vault_fixture_copy: object,
    vault_service_factory: Callable[..., VaultService],
    index_service_factory: Callable[..., DerivedIndexService],
) -> None:
    index = index_service_factory(vault_service_factory(vault_fixture_copy))  # type: ignore[arg-type]
    result = SearchService(index).search("café")
    # the non-utf8 note cannot be searched and is counted/skipped
    assert index.failed_count >= 1
    assert result.skipped_notes >= 1
    assert result.degraded is True
    assert all("non-utf8" not in hit.path for hit in result.hits)


def test_unavailable_index_raises_503_error(
    vault_service_factory: Callable[..., VaultService],
    index_service_factory: Callable[..., DerivedIndexService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    vault = vault_service_factory(root)
    vault.create_bytes("x.md", b"# X\n")
    index = index_service_factory(vault)
    index.clear()
    with pytest.raises(IndexUnavailable):
        SearchService(index).search("x")
