"""Render common local documents to a browser-safe PDF preview.

Office files are kept unchanged in the Vault.  When a preview is requested the
service copies the validated Vault bytes into a short-lived temporary directory
and invokes the local document converter without a shell.  The default
``dsh-doc`` route uses an isolated LibreOffice profile so missing Microsoft
Chinese fonts can be mapped to an installed equivalent.  PDF files are streamed
directly; DOC/DOCX, PPT/PPTX, XLS/XLSX and common OpenDocument formats are
converted to a temporary PDF first.
"""

from __future__ import annotations

import os
import shutil
import signal
import stat
import subprocess
from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import BinaryIO
from xml.sax.saxutils import escape as xml_escape

from ..config import DocumentPreviewSettings
from ..vault.service import VaultService
from .errors import DocumentPreviewError, DocumentPreviewErrorCode

_COPY_CHUNK_SIZE = 1024 * 1024
_STREAM_CHUNK_SIZE = 1024 * 1024
_DOCUMENT_SUFFIXES = frozenset(
    {
        ".pdf",
        ".doc",
        ".docx",
        ".docm",
        ".dotx",
        ".dotm",
        ".wps",
        ".wpt",
        ".ppt",
        ".pptx",
        ".pptm",
        ".pps",
        ".ppsx",
        ".potx",
        ".potm",
        ".dps",
        ".dpt",
        ".xls",
        ".xlsx",
        ".xlsm",
        ".et",
        ".ett",
        ".odt",
        ".ods",
        ".odp",
        ".ott",
        ".otp",
        ".ots",
        ".rtf",
        ".csv",
    }
)
_DOCUMENT_MIME_TYPES = frozenset(
    {
        "application/pdf",
        "application/msword",
        "application/rtf",
        "text/rtf",
        "text/csv",
        "application/vnd.ms-powerpoint",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-word.document.macroenabled.12",
        "application/vnd.ms-powerpoint.presentation.macroenabled.12",
        "application/vnd.ms-excel.sheet.macroenabled.12",
        "application/vnd.oasis.opendocument.text",
        "application/vnd.oasis.opendocument.spreadsheet",
        "application/vnd.oasis.opendocument.presentation",
    }
)

# The imported deck uses Microsoft YaHei (微软雅黑), which is not installed on
# this macOS host. LibreOffice otherwise falls back to FangSong, changing the
# visual weight and spacing. Each replacement is applied only when the source
# font is unavailable and a local sans/serif equivalent can be resolved.
_FONT_FALLBACKS = (
    ("微软雅黑", ("Hiragino Sans GB", "Noto Sans CJK SC", "Heiti SC")),
    ("Microsoft YaHei", ("Hiragino Sans GB", "Noto Sans CJK SC", "Heiti SC")),
    ("Microsoft YaHei UI", ("Hiragino Sans GB", "Noto Sans CJK SC", "Heiti SC")),
    ("宋体", ("Songti SC", "STSong", "Noto Serif CJK SC")),
    ("SimSun", ("Songti SC", "STSong", "Noto Serif CJK SC")),
    ("黑体", ("Heiti SC", "Hiragino Sans GB", "Noto Sans CJK SC")),
    ("SimHei", ("Heiti SC", "Hiragino Sans GB", "Noto Sans CJK SC")),
)


def is_document_attachment(name: str, content_type: str | None = None) -> bool:
    """Return whether a Vault file is a supported document preview target."""
    if (
        isinstance(content_type, str)
        and content_type.lower().split(";", 1)[0] in _DOCUMENT_MIME_TYPES
    ):
        return True
    clean_name = name.split("?", 1)[0].split("#", 1)[0]
    return Path(clean_name).suffix.lower() in _DOCUMENT_SUFFIXES


class DocumentPreviewArtifact:
    """A bounded PDF stream whose temporary resources live until it is closed."""

    def __init__(
        self,
        *,
        content_length: int,
        reader: BinaryIO | None = None,
        pdf_path: Path | None = None,
        temporary: TemporaryDirectory[str] | None = None,
    ) -> None:
        self.content_length = content_length
        self._reader = reader
        self._pdf_path = pdf_path
        self._temporary = temporary
        self._closed = False

    def stream(self) -> Iterator[bytes]:
        reader = self._reader
        opened_here = False
        try:
            if reader is None:
                if self._pdf_path is None:
                    return
                reader = self._pdf_path.open("rb")
                opened_here = True
            remaining = self.content_length
            while remaining > 0:
                chunk = reader.read(min(_STREAM_CHUNK_SIZE, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk
        finally:
            if opened_here and reader is not None:
                reader.close()
            self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._reader is not None:
            try:
                self._reader.close()
            except OSError:
                pass
            self._reader = None
        if self._temporary is not None:
            self._temporary.cleanup()
            self._temporary = None


class DocumentPreviewService:
    """Validate a Vault document and expose a browser-readable PDF artifact."""

    def __init__(self, vault: VaultService, settings: DocumentPreviewSettings) -> None:
        self.vault = vault
        self.settings = settings

    def open_preview(self, relative_path: str) -> DocumentPreviewArtifact:
        if not self.settings.enabled:
            raise DocumentPreviewError(
                DocumentPreviewErrorCode.DISABLED,
                "Document preview is disabled",
                status_code=503,
            )

        resource = self.vault.open_resource(relative_path)
        canonical_path = str(resource["path"])
        content_type = resource.get("content_type")
        if not is_document_attachment(canonical_path, content_type):
            self.vault.close_resource(resource)
            raise DocumentPreviewError(
                DocumentPreviewErrorCode.NOT_PREVIEWABLE,
                "The selected file is not a supported document",
                status_code=400,
                path=canonical_path,
            )
        suffix = Path(canonical_path).suffix.lower()
        if suffix == ".pdf":
            if int(resource["byte_length"]) > self.settings.max_output_bytes:
                self.vault.close_resource(resource)
                raise DocumentPreviewError(
                    DocumentPreviewErrorCode.TOO_LARGE,
                    "The document preview is too large",
                    status_code=413,
                    path=canonical_path,
                )
            reader = resource["reader"]
            resource["reader"] = None
            return DocumentPreviewArtifact(
                content_length=int(resource["byte_length"]),
                reader=reader,
            )

        temporary: TemporaryDirectory[str] | None = TemporaryDirectory(
            prefix="localnote-document-preview-"
        )
        try:
            workdir = Path(temporary.name)
            input_path = workdir / f"source{suffix or '.document'}"
            output_dir = workdir / "output"
            output_dir.mkdir()
            self._copy_resource(
                resource,
                input_path,
                canonical_path,
                max_bytes=self.vault.max_file_bytes,
            )
            self.vault.close_resource(resource)
            resource = None
            output_path = self._render(input_path, output_dir, workdir, canonical_path)
            try:
                output_size = output_path.stat().st_size
            except OSError as exc:
                raise DocumentPreviewError(
                    DocumentPreviewErrorCode.FAILED,
                    "The local document preview could not be read",
                    status_code=502,
                    path=canonical_path,
                ) from exc
            if output_size <= 0:
                raise DocumentPreviewError(
                    DocumentPreviewErrorCode.FAILED,
                    "The document preview was empty",
                    status_code=502,
                    path=canonical_path,
                )
            if output_size > self.settings.max_output_bytes:
                raise DocumentPreviewError(
                    DocumentPreviewErrorCode.TOO_LARGE,
                    "The document preview is too large",
                    status_code=413,
                    path=canonical_path,
                )
            return DocumentPreviewArtifact(
                content_length=output_size,
                pdf_path=output_path,
                temporary=temporary,
            )
        except Exception:
            if temporary is not None:
                temporary.cleanup()
            raise
        finally:
            if resource is not None:
                self.vault.close_resource(resource)

    @staticmethod
    def _copy_resource(
        resource: dict[str, object],
        destination: Path,
        path: str,
        *,
        max_bytes: int,
    ) -> None:
        reader = resource["reader"]
        total = 0
        try:
            with destination.open("wb") as output:
                while True:
                    chunk = reader.read(_COPY_CHUNK_SIZE)  # type: ignore[union-attr]
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise DocumentPreviewError(
                            DocumentPreviewErrorCode.TOO_LARGE,
                            "The document is too large to preview",
                            status_code=413,
                            path=path,
                        )
                    output.write(chunk)
        except DocumentPreviewError:
            raise
        except OSError as exc:
            raise DocumentPreviewError(
                DocumentPreviewErrorCode.FAILED,
                "The document could not be prepared for preview",
                status_code=500,
                path=path,
            ) from exc

    @staticmethod
    def _font_available(name: str) -> bool:
        fc_match = shutil.which("fc-match")
        if not fc_match:
            return False
        try:
            result = subprocess.run(
                [fc_match, "-f", "%{family}", name],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                check=False,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return name.casefold() in result.stdout.casefold()

    @classmethod
    def _font_substitutions(cls) -> list[tuple[str, str]]:
        substitutions: list[tuple[str, str]] = []
        for source, candidates in _FONT_FALLBACKS:
            if cls._font_available(source):
                continue
            replacement = next(
                (candidate for candidate in candidates if cls._font_available(candidate)),
                None,
            )
            if replacement:
                substitutions.append((source, replacement))
        return substitutions

    @classmethod
    def _prepare_libreoffice_profile(cls, workdir: Path) -> Path:
        profile = workdir / "lo-profile"
        user = profile / "user"
        user.mkdir(parents=True, exist_ok=True)
        substitutions = cls._font_substitutions()
        if not substitutions:
            return profile
        entries = []
        for index, (source, replacement) in enumerate(substitutions):
            entries.append(
                f"""<node oor:name=\"_{index}\" oor:op=\"replace\">
<prop oor:name=\"Always\" oor:op=\"fuse\"><value>true</value></prop>
<prop oor:name=\"ReplaceFont\" oor:op=\"fuse\"><value>{xml_escape(source)}</value></prop>
<prop oor:name=\"OnScreenOnly\" oor:op=\"fuse\"><value>false</value></prop>
<prop oor:name=\"SubstituteFont\" oor:op=\"fuse\"><value>{xml_escape(replacement)}</value></prop>
</node>"""
            )
        registry = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<oor:items xmlns:oor=\"http://openoffice.org/2001/registry\" xmlns:xs=\"http://www.w3.org/2001/XMLSchema\">
  <item oor:path=\"/org.openoffice.Office.Common/Font/Substitution\">
    <prop oor:name=\"Replacement\" oor:op=\"fuse\"><value>true</value></prop>
  </item>
  <item oor:path=\"/org.openoffice.Office.Common/Font/Substitution/FontPairs\">{entries}</item>
</oor:items>
""".format(entries="".join(entries))
        (user / "registrymodifications.xcu").write_text(registry, encoding="utf-8")
        return profile

    @classmethod
    def _render_argv(
        cls,
        command: str,
        input_path: Path,
        output_dir: Path,
        workdir: Path,
    ) -> list[str]:
        # dsh-doc delegates Office PDF rendering to LibreOffice. Calling
        # soffice directly here lets us give it an isolated profile with font
        # substitutions, while custom test/operator commands keep their exact
        # configured argv contract.
        if Path(command).name.casefold() == "dsh-doc":
            soffice = shutil.which("soffice") or shutil.which("libreoffice")
            if soffice:
                profile = cls._prepare_libreoffice_profile(workdir)
                return [
                    soffice,
                    "--headless",
                    "--nologo",
                    "--nodefault",
                    "--nofirststartwizard",
                    f"-env:UserInstallation={profile.as_uri()}",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    str(output_dir),
                    str(input_path),
                ]
        return [command, "render", str(input_path), "--outdir", str(output_dir), "--pdf"]

    def _render(
        self,
        input_path: Path,
        output_dir: Path,
        workdir: Path,
        path: str,
    ) -> Path:
        command = self._tool_command()
        argv = self._render_argv(command, input_path, output_dir, workdir)
        try:
            process = subprocess.Popen(
                argv,
                cwd=workdir,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                start_new_session=True,
            )
            try:
                process.wait(timeout=self.settings.timeout_seconds)
            except subprocess.TimeoutExpired as exc:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except (AttributeError, PermissionError, ProcessLookupError):
                    process.kill()
                process.wait()
                raise DocumentPreviewError(
                    DocumentPreviewErrorCode.TIMEOUT,
                    "Document preview timed out",
                    status_code=504,
                    path=path,
                ) from exc
        except DocumentPreviewError:
            raise
        except OSError as exc:
            raise DocumentPreviewError(
                DocumentPreviewErrorCode.UNAVAILABLE,
                "The local document preview tool could not be started",
                status_code=503,
                path=path,
            ) from exc
        if process.returncode != 0:
            raise DocumentPreviewError(
                DocumentPreviewErrorCode.FAILED,
                "The local document preview tool failed",
                status_code=502,
                path=path,
            )
        try:
            candidates = sorted(
                candidate
                for candidate in output_dir.iterdir()
                if (
                    candidate.is_file()
                    and not candidate.is_symlink()
                    and candidate.suffix.lower() == ".pdf"
                )
            )
        except OSError as exc:
            raise DocumentPreviewError(
                DocumentPreviewErrorCode.FAILED,
                "The local document preview could not be read",
                status_code=502,
                path=path,
            ) from exc
        if not candidates:
            raise DocumentPreviewError(
                DocumentPreviewErrorCode.FAILED,
                "The local document preview tool returned no PDF",
                status_code=502,
                path=path,
            )
        return candidates[0]

    def _tool_command(self) -> str:
        configured = self.settings.command
        found = shutil.which(configured)
        if found:
            return found
        if configured == "dsh-doc":
            fallback = Path.home() / ".local" / "bin" / "dsh-doc"
            try:
                if fallback.is_file() and not stat.S_ISDIR(fallback.stat().st_mode):
                    return str(fallback)
            except OSError:
                pass
        raise DocumentPreviewError(
            DocumentPreviewErrorCode.UNAVAILABLE,
            "No local document preview tool was found; install or configure dsh-doc",
            status_code=503,
        )


__all__ = ["DocumentPreviewArtifact", "DocumentPreviewService", "is_document_attachment"]
