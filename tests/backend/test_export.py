"""Note export: reference resolution, attachment inlining and the REST contract.

The service layer is exercised against a real temporary Vault (the only place
that touches the filesystem); the route layer goes through FastAPI so the
download headers and the error body shape stay pinned.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest
from tests.backend.client import TestClient

from server.api import dependencies
from server.api.main import create_app
from server.export.errors import NoteNotMarkdown
from server.export.service import ExportService
from server.vault.service import VaultService

#: 1×1 transparent PNG — small, valid, and distinguishable from text.
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@pytest.fixture
def vault(tmp_path: Path) -> VaultService:
    root = tmp_path / "vault"
    root.mkdir(parents=True, exist_ok=True)
    service = VaultService(root, watcher_enabled=False)
    service.initialize(start_watcher=False)
    return service


def write(vault: VaultService, relative: str, data: bytes) -> None:
    target = vault.root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def write_note(vault: VaultService, relative: str, text: str) -> None:
    write(vault, relative, text.encode("utf-8"))


@pytest.fixture
def export_client(vault: VaultService):
    app = create_app()
    app.state.vault_service = vault
    app.dependency_overrides[dependencies.get_vault_service] = lambda: vault
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# service layer
# ---------------------------------------------------------------------------


def test_relative_image_is_rewritten_and_inlined(vault: VaultService) -> None:
    write(vault, "Notes/pic.png", PNG_BYTES)
    write_note(vault, "Notes/A.md", "# A\n\n![pic](pic.png)\n")

    result = ExportService(vault).prepare("Notes/A.md")

    assert result.path == "Notes/A.md"
    assert result.title == "A"
    assert result.download_name == "A.md"
    # The render/print form keeps a plain Vault-relative reference …
    assert "![pic](<Notes/pic.png>)" in result.markdown
    # … while the downloadable file carries the bytes themselves.
    assert "data:image/png;base64," in result.inlined_markdown
    assert "pic.png)" not in result.inlined_markdown.replace("data:image/png;base64,", "")
    assert [item.path for item in result.attachments] == ["Notes/pic.png"]
    assert result.attachments[0].inlined is True
    assert result.attachments[0].mime == "image/png"
    assert result.inlined_bytes == len(PNG_BYTES)
    assert result.warnings == []
    assert result.truncated is False


def test_obsidian_embed_resolves_by_basename(vault: VaultService) -> None:
    write(vault, "assets/pic.png", PNG_BYTES)
    write_note(vault, "Notes/A.md", "![[pic.png]]\n")

    result = ExportService(vault).prepare("Notes/A.md")

    assert "![pic.png](<assets/pic.png>)" in result.markdown
    assert [item.path for item in result.attachments] == ["assets/pic.png"]


def test_embed_with_display_text_and_spaces_is_encoded(vault: VaultService) -> None:
    write(vault, "assets/my pic.png", PNG_BYTES)
    write_note(vault, "A.md", "![[my pic.png|别名]]\n")

    result = ExportService(vault).prepare("A.md")

    assert "![别名](<assets/my%20pic.png>)" in result.markdown


def test_external_and_anchor_targets_are_untouched(vault: VaultService) -> None:
    write_note(vault, "A.md", "[web](https://example.com/a.png)\n\n[top](#top)\n\n![d](data:image/png;base64,AAAA)\n")

    result = ExportService(vault).prepare("A.md")

    assert result.markdown == result.inlined_markdown
    assert "https://example.com/a.png" in result.markdown
    assert result.attachments == []


def test_missing_attachment_warns_and_keeps_the_source(vault: VaultService) -> None:
    write_note(vault, "Notes/A.md", "![gone](nope.png)\n")

    result = ExportService(vault).prepare("Notes/A.md")

    assert "![gone](nope.png)" in result.markdown
    assert [warning["code"] for warning in result.warnings] == ["attachment_not_found"]
    assert result.attachments == []


def test_plain_wikilink_to_a_note_is_not_an_attachment(vault: VaultService) -> None:
    write_note(vault, "Notes/A.md", "See [[B]] for details.\n")
    write_note(vault, "Notes/B.md", "# B\n")

    result = ExportService(vault).prepare("Notes/A.md")

    assert result.markdown == "See [[B]] for details.\n"
    assert result.warnings == []


def test_code_fences_and_inline_code_are_never_rewritten(vault: VaultService) -> None:
    write(vault, "Notes/pic.png", PNG_BYTES)
    source = "```\n![in fence](pic.png)\n```\n\ninline `![in code](pic.png)` stays\n\n![real](pic.png)\n"
    write_note(vault, "Notes/A.md", source)

    result = ExportService(vault).prepare("Notes/A.md")

    assert "![in fence](pic.png)" in result.markdown
    assert "`![in code](pic.png)`" in result.markdown
    assert "![real](<Notes/pic.png>)" in result.markdown
    assert len(result.attachments) == 1


def test_traversal_target_is_never_resolved(vault: VaultService, tmp_path: Path) -> None:
    outside = tmp_path / "outside.png"
    outside.write_bytes(PNG_BYTES)
    write_note(vault, "Notes/A.md", "![out](../outside.png)\n")

    result = ExportService(vault).prepare("Notes/A.md")

    assert "![out](../outside.png)" in result.markdown
    assert [warning["code"] for warning in result.warnings] == ["attachment_not_found"]
    assert result.attachments == []


def test_oversized_attachment_degrades_instead_of_failing(vault: VaultService) -> None:
    write(vault, "Notes/big.png", PNG_BYTES)
    write_note(vault, "Notes/A.md", "![big](big.png)\n")

    result = ExportService(vault, max_attachment_bytes=4).prepare("Notes/A.md")

    assert result.truncated is True
    assert "![big](big.png)" in result.inlined_markdown
    assert [item.reason for item in result.attachments] == ["attachment_too_large"]
    assert result.attachments[0].inlined is False


def test_total_budget_stops_inlining_after_the_cap(vault: VaultService) -> None:
    write(vault, "Notes/one.png", PNG_BYTES)
    write(vault, "Notes/two.png", PNG_BYTES)
    write_note(vault, "Notes/A.md", "![one](one.png)\n\n![two](two.png)\n")

    result = ExportService(vault, max_total_bytes=len(PNG_BYTES)).prepare("Notes/A.md")

    assert result.truncated is True
    assert [item.inlined for item in result.attachments] == [True, False]
    assert result.attachments[1].reason == "export_size_limit"


def test_same_attachment_twice_is_counted_once(vault: VaultService) -> None:
    write(vault, "Notes/pic.png", PNG_BYTES)
    write_note(vault, "Notes/A.md", "![a](pic.png)\n\n![b](pic.png)\n")

    result = ExportService(vault).prepare("Notes/A.md")

    assert len(result.attachments) == 1
    assert result.inlined_bytes == len(PNG_BYTES)
    assert result.inlined_markdown.count("data:image/png;base64,") == 2


def test_html_image_src_is_rewritten(vault: VaultService) -> None:
    write(vault, "Notes/pic.png", PNG_BYTES)
    write_note(vault, "Notes/A.md", '<img src="pic.png" width="10">\n')

    result = ExportService(vault).prepare("Notes/A.md")

    assert '<img src="Notes/pic.png" width="10">' in result.markdown
    assert "data:image/png;base64," in result.inlined_markdown


def test_non_markdown_target_is_rejected(vault: VaultService) -> None:
    write(vault, "Notes/pic.png", PNG_BYTES)

    with pytest.raises(NoteNotMarkdown):
        ExportService(vault).prepare("Notes/pic.png")


def test_unknown_note_raises_vault_not_found(vault: VaultService) -> None:
    from server.vault.errors import PathNotFound

    with pytest.raises(PathNotFound):
        ExportService(vault).prepare("missing.md")


# ---------------------------------------------------------------------------
# REST contract
# ---------------------------------------------------------------------------


def test_manifest_endpoint_returns_references_and_payloads(export_client: TestClient, vault: VaultService) -> None:
    write(vault, "Notes/pic.png", PNG_BYTES)
    write_note(vault, "Notes/A.md", "![pic](pic.png)\n")

    response = export_client.get("/api/v1/export/note", params={"path": "Notes/A.md"})

    assert response.status_code == 200
    body = response.json()
    assert body["path"] == "Notes/A.md"
    assert body["download_name"] == "A.md"
    assert body["format"] == "manifest"
    assert "![pic](<Notes/pic.png>)" in body["markdown"]
    assert body["attachments"][0]["url"] == "Notes/pic.png"
    assert body["attachments"][0]["data_uri"].startswith("data:image/png;base64,")
    assert body["inlined_bytes"] == len(PNG_BYTES)


def test_markdown_endpoint_downloads_a_self_contained_file(export_client: TestClient, vault: VaultService) -> None:
    write(vault, "Notes/pic.png", PNG_BYTES)
    write_note(vault, "Notes/我的笔记.md", "![pic](pic.png)\n")

    response = export_client.get("/api/v1/export/markdown", params={"path": "Notes/我的笔记.md"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert "filename*=UTF-8''" in disposition
    assert "data:image/png;base64," in response.text
    assert response.headers["x-export-attachments"] == "1"
    assert response.headers["x-export-truncated"] == "0"


def test_non_markdown_export_returns_a_stable_error_body(export_client: TestClient, vault: VaultService) -> None:
    write(vault, "Notes/pic.png", PNG_BYTES)

    response = export_client.get("/api/v1/export/note", params={"path": "Notes/pic.png"})

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "note_not_markdown"
    assert error["path"] == "Notes/pic.png"


def test_traversal_path_is_rejected_by_the_vault_guard(export_client: TestClient) -> None:
    response = export_client.get("/api/v1/export/note", params={"path": "../outside.md"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "path_traversal"


def test_export_requires_a_configured_vault(tmp_path: Path) -> None:
    app = create_app()
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/api/v1/export/note", params={"path": "A.md"})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "vault_not_configured"
