"""M3 MetadataService matrix (PLAN-M3 §5.1/§9.1).

Reads current bytes through VaultService (fresh, not from the index); parse
failures are structured HTTP-200-style responses, never exceptions except for
missing notes (``PathNotFound`` → 404 at the API layer).
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from server.metadata.service import MetadataService
from server.vault.errors import PathNotFound
from server.vault.service import VaultService


def _service(vault: VaultService) -> MetadataService:
    return MetadataService(vault)


def test_ok_metadata_keeps_unknown_fields_and_normalizes_tags(
    vault_fixture_copy: object,
    vault_service_factory: Callable[..., VaultService],
) -> None:
    vault = vault_service_factory(vault_fixture_copy)  # type: ignore[arg-type]
    response = _service(vault).get("中文 note.md")
    assert response.path == "中文 note.md"
    assert response.frontmatter_status == "ok"
    assert response.title == "中文 note"
    assert response.tags == ["中文标签", "工作"]
    assert response.parse_error is None
    assert response.properties["emoji"] == "😀"
    assert response.properties["related"] == ["项目A", "项目B"]


def test_none_status_when_no_frontmatter(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: object,
) -> None:
    root = tmp_path  # type: ignore[assignment]
    from pathlib import Path

    Path(str(root)).mkdir(exist_ok=True)
    vault = vault_service_factory(Path(str(root)))
    vault.create_bytes("plain.md", b"# Plain\nno frontmatter here\n")
    response = _service(vault).get("plain.md")
    assert response.frontmatter_status == "none"
    assert response.properties == {}
    assert response.tags == []
    assert response.parse_error is None
    assert response.title == "Plain"


def test_parse_error_rendered_structurally(
    vault_fixture_copy: object,
    vault_service_factory: Callable[..., VaultService],
) -> None:
    vault = vault_service_factory(vault_fixture_copy)  # type: ignore[arg-type]
    response = _service(vault).get("frontmatter/bad-yaml.md")
    assert response.frontmatter_status == "parse_error"
    assert response.parse_error is not None
    assert response.parse_error.kind in {"yaml", "other"}
    assert response.properties == {}

    unterminated = _service(vault).get("frontmatter/unterminated.md")
    assert unterminated.frontmatter_status == "parse_error"
    assert unterminated.parse_error is not None
    assert unterminated.parse_error.kind == "unterminated"


def test_unreadable_non_utf8_note(
    vault_fixture_copy: object,
    vault_service_factory: Callable[..., VaultService],
) -> None:
    vault = vault_service_factory(vault_fixture_copy)  # type: ignore[arg-type]
    response = _service(vault).get("bytes/non-utf8.md")
    assert response.frontmatter_status == "unreadable"
    assert response.parse_error is not None
    assert response.parse_error.kind == "decode"
    assert response.properties == {}
    assert response.tags == []


def test_metadata_never_writes_back(
    vault_fixture_copy: object,
    vault_service_factory: Callable[..., VaultService],
) -> None:
    import hashlib
    from pathlib import Path

    root = Path(vault_fixture_copy)  # type: ignore[arg-type]
    target = root / "中文 note.md"
    before = target.read_bytes()
    before_hash = hashlib.sha256(before).hexdigest()
    vault = vault_service_factory(root)
    _service(vault).get("中文 note.md")
    assert hashlib.sha256(target.read_bytes()).hexdigest() == before_hash


def test_missing_note_raises_path_not_found(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: object,
) -> None:
    from pathlib import Path

    root = Path(tmp_path)  # type: ignore[arg-type]
    root.mkdir(exist_ok=True)
    vault = vault_service_factory(root)
    with pytest.raises(PathNotFound):
        _service(vault).get("missing.md")


def test_crlf_bom_note_metadata(
    vault_fixture_copy: object,
    vault_service_factory: Callable[..., VaultService],
) -> None:
    vault = vault_service_factory(vault_fixture_copy)  # type: ignore[arg-type]
    response = _service(vault).get("frontmatter/crlf.md")
    assert response.frontmatter_status == "ok"
    assert response.properties["title"] == "CRLF 笔记"
    assert response.tags == ["crlf", "second"]
