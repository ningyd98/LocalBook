"""Local Office/PDF document preview contracts."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from server.api.main import create_app
from server.config import DocumentPreviewSettings, Settings, VaultSettings
from server.documents.errors import DocumentPreviewError, DocumentPreviewErrorCode
from server.documents.service import DocumentPreviewService, is_document_attachment
from server.vault.service import VaultService
from tests.backend.client import TestClient

_PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n"


def _fake_renderer(tmp_path: Path) -> Path:
    script = tmp_path / "fake-document-renderer.py"
    script.write_text(
        f"#!{sys.executable}\n"
        "import pathlib\n"
        "import sys\n"
        "args = sys.argv\n"
        "outdir = pathlib.Path(args[args.index('--outdir') + 1])\n"
        "(outdir / 'source.pdf').write_bytes(b'%PDF-1.4\\n%%EOF\\n')\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | 0o111)
    return script


def _service(root: Path, renderer: Path) -> DocumentPreviewService:
    vault = VaultService(root, watcher_enabled=False)
    vault.initialize(start_watcher=False)
    return DocumentPreviewService(
        vault,
        DocumentPreviewSettings(command=str(renderer), timeout_seconds=10),
    )


def test_common_document_extensions_are_previewable() -> None:
    for name in (
        "report.doc",
        "report.docx",
        "slides.ppt",
        "slides.pptx",
        "table.xls",
        "table.xlsx",
        "report.pdf",
    ):
        assert is_document_attachment(name)


def test_pdf_is_streamed_without_invoking_converter(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "report.pdf").write_bytes(_PDF)
    service = _service(root, tmp_path / "not-used")

    artifact = service.open_preview("report.pdf")
    try:
        assert b"".join(artifact.stream()) == _PDF
    finally:
        artifact.close()


def test_office_file_is_converted_to_a_temporary_pdf(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "report.docx").write_bytes(b"fake docx")
    service = _service(root, _fake_renderer(tmp_path))

    artifact = service.open_preview("report.docx")
    try:
        assert b"%PDF-1.4" in b"".join(artifact.stream())
    finally:
        artifact.close()


def test_unsupported_file_has_stable_error(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "photo.png").write_bytes(b"png")
    service = _service(root, _fake_renderer(tmp_path))

    with pytest.raises(DocumentPreviewError) as raised:
        service.open_preview("photo.png")

    assert raised.value.code is DocumentPreviewErrorCode.NOT_PREVIEWABLE
    assert raised.value.status_code == 400
    assert raised.value.path == "photo.png"


def test_libreoffice_profile_maps_missing_microsoft_fonts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        DocumentPreviewService,
        "_font_available",
        staticmethod(lambda name: name == "Hiragino Sans GB"),
    )

    profile = DocumentPreviewService._prepare_libreoffice_profile(tmp_path)
    registry = (profile / "user" / "registrymodifications.xcu").read_text(encoding="utf-8")

    assert "<prop oor:name=\"Replacement\"" in registry
    assert "<value>微软雅黑</value>" in registry
    assert "<value>Hiragino Sans GB</value>" in registry


def test_missing_document_tool_has_stable_error(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "report.docx").write_bytes(b"fake docx")
    vault = VaultService(root, watcher_enabled=False)
    vault.initialize(start_watcher=False)
    service = DocumentPreviewService(
        vault,
        DocumentPreviewSettings(command="definitely-not-installed"),
    )

    with pytest.raises(DocumentPreviewError) as raised:
        service.open_preview("report.docx")

    assert raised.value.code is DocumentPreviewErrorCode.UNAVAILABLE
    assert str(root) not in raised.value.message


def test_document_preview_route_returns_pdf(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "report.docx").write_bytes(b"fake docx")
    settings = Settings(
        vault=VaultSettings(root=root, watcher_enabled=False),
        document_preview=DocumentPreviewSettings(command=str(_fake_renderer(tmp_path))),
    )
    client = TestClient(create_app(settings))

    response = client.get("/api/v1/vault/document-preview", params={"path": "report.docx"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.content.startswith(b"%PDF-1.4")
