"""M3 LinksService / backlinks matrix (PLAN-M3 §5.2/§9.1)."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from server.index.errors import IndexUnavailable
from server.index.service import DerivedIndexService
from server.links.service import LinksService
from server.vault.errors import PathNotFound
from server.vault.service import VaultService


def test_outgoing_resolved_broken_and_ambiguous(
    vault_fixture_copy: object,
    vault_service_factory: Callable[..., VaultService],
    index_service_factory: Callable[..., DerivedIndexService],
) -> None:
    index = index_service_factory(vault_service_factory(vault_fixture_copy))  # type: ignore[arg-type]
    response = LinksService(index).outgoing("notes/a.md")
    assert response.path == "notes/a.md"
    assert response.broken_count == 1
    by_target = {ref.target: ref for ref in response.outgoing}
    assert by_target["Ref A"].resolved_path == "notes/Ref A.md"
    assert by_target["Cfg B"].broken is True
    assert by_target["Alpha"].ambiguous is True
    assert by_target["image.png"].kind == "embed"
    # LinkRef payload carries no internal context field (extra=forbid)
    assert set(by_target["Ref A"].model_dump()) == {
        "target",
        "raw",
        "kind",
        "display",
        "section",
        "block",
        "resolved_path",
        "broken",
        "ambiguous",
        "candidates",
    }


def test_backlinks_reverse_with_context(
    vault_fixture_copy: object,
    vault_service_factory: Callable[..., VaultService],
    index_service_factory: Callable[..., DerivedIndexService],
) -> None:
    index = index_service_factory(vault_service_factory(vault_fixture_copy))  # type: ignore[arg-type]
    response = LinksService(index).backlinks("notes/Ref A.md")
    assert response.count == 1
    ref = response.backlinks[0]
    assert ref.source_path == "notes/a.md"
    assert ref.title == "A"
    assert ref.text is not None
    assert "[[Ref A]]" in ref.text


def test_self_heading_is_not_an_outgoing_backlink(
    vault_fixture_copy: object,
    vault_service_factory: Callable[..., VaultService],
    index_service_factory: Callable[..., DerivedIndexService],
) -> None:
    index = index_service_factory(vault_service_factory(vault_fixture_copy))  # type: ignore[arg-type]
    links = LinksService(index)
    combo = links.outgoing("combo.md")
    self_anchor = [ref for ref in combo.outgoing if ref.target == ""]
    assert len(self_anchor) == 1
    assert self_anchor[0].section == "SelfHeading"
    sources = [ref.source_path for ref in links.backlinks("combo.md").backlinks]
    assert "combo.md" not in sources  # self anchors never become backlinks
    assert "notes/Ref A.md" in sources


def test_missing_note_is_path_not_found(
    vault_fixture_copy: object,
    vault_service_factory: Callable[..., VaultService],
    index_service_factory: Callable[..., DerivedIndexService],
) -> None:
    index = index_service_factory(vault_service_factory(vault_fixture_copy))  # type: ignore[arg-type]
    with pytest.raises(PathNotFound):
        LinksService(index).outgoing("never-existed.md")
    with pytest.raises(PathNotFound):
        LinksService(index).backlinks("never-existed.md")


def test_unavailable_index_raises(
    vault_service_factory: Callable[..., VaultService],
    index_service_factory: Callable[..., DerivedIndexService],
    tmp_path: object,
) -> None:
    from pathlib import Path

    root = Path(tmp_path)  # type: ignore[arg-type]
    root.mkdir(exist_ok=True)
    vault = vault_service_factory(root)
    vault.create_bytes("x.md", b"# X\n")
    index = index_service_factory(vault)
    index.clear()  # simulates a failed build (state idle)
    with pytest.raises(IndexUnavailable):
        LinksService(index).outgoing("x.md")


def test_web_links_are_not_broken(
    vault_fixture_copy: object,
    vault_service_factory: Callable[..., VaultService],
    index_service_factory: Callable[..., DerivedIndexService],
) -> None:
    index = index_service_factory(vault_service_factory(vault_fixture_copy))  # type: ignore[arg-type]
    combo = LinksService(index).outgoing("combo.md")
    web = [ref for ref in combo.outgoing if ref.kind == "web"]
    assert len(web) == 1
    assert web[0].broken is False
    assert web[0].resolved_path is None
