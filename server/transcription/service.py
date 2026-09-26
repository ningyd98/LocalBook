"""Run a configured local speech-to-text tool over a Vault audio file.

The browser can request a transcription only by sending a Vault-relative path.
The command itself is operator configuration, parsed with ``shlex`` and run with
``shell=False``.  Audio bytes are copied through ``VaultService.open_resource``
to a short-lived temporary directory so the external process never receives a
Vault path and cannot follow a later symlink swap in the user's library.
"""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from ..config import TranscriptionSettings
from ..vault.attachments import is_audio_attachment
from ..vault.service import VaultService
from .errors import TranscriptionError, TranscriptionErrorCode

log = logging.getLogger("localnote.transcription")
_COPY_CHUNK_SIZE = 1024 * 1024
_DEFAULT_COMMAND_TEMPLATE = (
    "whisper --model {model} --output_dir {output_dir} "
    "--output_format txt {input}"
)
_PLACEHOLDERS = ("{input}", "{output_dir}", "{output}", "{model}", "{language}")


class LocalTranscriptionService:
    """Bounded, local-only adapter for Whisper-compatible command line tools."""

    def __init__(self, vault: VaultService, settings: TranscriptionSettings) -> None:
        self.vault = vault
        self.settings = settings

    def transcribe(self, relative_path: str, *, language: str | None = None) -> dict[str, Any]:
        if not self.settings.enabled:
            raise TranscriptionError(
                TranscriptionErrorCode.DISABLED,
                "Local transcription is disabled",
                status_code=503,
            )

        resource = self.vault.open_resource(relative_path)
        canonical_path = str(resource["path"])
        if not is_audio_attachment(canonical_path, resource.get("content_type")):
            self.vault.close_resource(resource)
            raise TranscriptionError(
                TranscriptionErrorCode.NOT_AUDIO,
                "The selected file is not an audio recording",
                status_code=400,
                path=canonical_path,
            )

        requested_language = language or self.settings.language
        try:
            with tempfile.TemporaryDirectory(prefix="localnote-transcription-") as temporary:
                workdir = Path(temporary)
                output_dir = workdir / "output"
                output_dir.mkdir()
                suffix = Path(canonical_path).suffix.lower()
                if not suffix or len(suffix) > 16 or not suffix[1:].isalnum():
                    suffix = ".audio"
                input_path = workdir / f"recording{suffix}"
                self._copy_resource(resource, input_path)
                command, expected_output = self._build_command(
                    input_path=input_path,
                    output_dir=output_dir,
                    requested_language=requested_language,
                )
                self.vault.close_resource(resource)
                resource = None
                tool_name = Path(command[0]).name or command[0]
                self._ensure_tool(command[0])
                stdout = self._run(command, workdir=workdir, tool_name=tool_name)
                text = self._read_result(output_dir, expected_output, stdout)
                truncated = len(text) > self.settings.max_output_chars
                if truncated:
                    text = text[: self.settings.max_output_chars].rstrip()
                if not text:
                    raise TranscriptionError(
                        TranscriptionErrorCode.FAILED,
                        "The local transcription tool returned no text",
                        status_code=502,
                        path=canonical_path,
                    )
                return {
                    "path": canonical_path,
                    "text": text,
                    "language": requested_language,
                    "tool": tool_name,
                    "truncated": truncated,
                }
        finally:
            if resource is not None:
                self.vault.close_resource(resource)

    def _copy_resource(self, resource: dict[str, Any], destination: Path) -> None:
        reader = resource["reader"]
        try:
            with destination.open("wb") as output:
                while True:
                    chunk = reader.read(_COPY_CHUNK_SIZE)
                    if not chunk:
                        break
                    output.write(chunk)
        except OSError as exc:
            raise TranscriptionError(
                TranscriptionErrorCode.FAILED,
                "The audio recording could not be prepared for transcription",
                status_code=500,
                path=str(resource["path"]),
            ) from exc

    def _build_command(
        self,
        *,
        input_path: Path,
        output_dir: Path,
        requested_language: str | None,
    ) -> tuple[list[str], Path]:
        template = self.settings.command_template
        try:
            tokens = shlex.split(template, posix=True)
        except ValueError as exc:
            raise TranscriptionError(
                TranscriptionErrorCode.UNAVAILABLE,
                "The local transcription command is not configured correctly",
                status_code=503,
            ) from exc
        if not tokens:
            raise TranscriptionError(
                TranscriptionErrorCode.UNAVAILABLE,
                "No local transcription command is configured",
                status_code=503,
            )

        output_path = output_dir / "transcript.txt"
        values = {
            "{input}": str(input_path),
            "{output_dir}": str(output_dir),
            "{output}": str(output_path),
            "{model}": self.settings.model,
        }
        command: list[str] = []
        language_placeholder_used = False
        for token in tokens:
            if token == "{language}":
                language_placeholder_used = True
                if requested_language:
                    command.append(requested_language)
                continue
            replaced = token
            for placeholder, value in values.items():
                replaced = replaced.replace(placeholder, value)
            command.append(replaced)

        if "{input}" not in template:
            command.append(str(input_path))
        if template == _DEFAULT_COMMAND_TEMPLATE and "--model_dir" not in command:
            model_dir = self.settings.model_dir or (
                self.vault.root / ".localnote" / "whisper-models"
            )
            command.extend(("--model_dir", str(model_dir)))
        if (
            requested_language
            and not language_placeholder_used
            and template == _DEFAULT_COMMAND_TEMPLATE
        ):
            command.extend(("--language", requested_language))
        if "{output}" not in template:
            expected_output = output_dir / f"{input_path.stem}.txt"
        else:
            expected_output = output_path
        return command, expected_output

    @staticmethod
    def _ensure_tool(executable: str) -> None:
        if shutil.which(executable) is None:
            raise TranscriptionError(
                TranscriptionErrorCode.UNAVAILABLE,
                "No local transcription tool was found; configure a Whisper-compatible command",
                status_code=503,
            )

    def _run(self, command: list[str], *, workdir: Path, tool_name: str) -> str:
        stdout_path = workdir / "tool.stdout"
        process: subprocess.Popen[str] | None = None
        try:
            with stdout_path.open("wb") as stdout_file:
                process = subprocess.Popen(
                    command,
                    cwd=workdir,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_file,
                    stderr=subprocess.DEVNULL,
                    shell=False,
                    start_new_session=True,
                )
                process.wait(timeout=self.settings.timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            if process is not None:
                # Popen started a new session, so all tool subprocesses share
                # its process group. Killing only the direct child lets model
                # workers keep running after the request has timed out.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except OSError:
                    log.exception("could not stop transcription process group")
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                process.wait()
            raise TranscriptionError(
                TranscriptionErrorCode.TIMEOUT,
                "Local transcription timed out",
                status_code=504,
            ) from exc
        except OSError as exc:
            raise TranscriptionError(
                TranscriptionErrorCode.UNAVAILABLE,
                "The local transcription tool could not be started",
                status_code=503,
            ) from exc
        if process is None:
            raise TranscriptionError(
                TranscriptionErrorCode.UNAVAILABLE,
                "The local transcription tool could not be started",
                status_code=503,
            )
        if process.returncode != 0:
            log.info(
                "transcription tool failed tool=%s returncode=%s",
                tool_name,
                process.returncode,
            )
            raise TranscriptionError(
                TranscriptionErrorCode.FAILED,
                "The local transcription tool failed",
                status_code=502,
            )
        try:
            with stdout_path.open("r", encoding="utf-8", errors="replace") as output:
                return output.read(self.settings.max_output_chars + 1)
        except OSError:
            return ""

    def _read_result(self, output_dir: Path, expected_output: Path, stdout: str) -> str:
        candidates: list[Path] = []
        if expected_output.is_file():
            candidates.append(expected_output)
        if not candidates and output_dir.is_dir():
            candidates = sorted(
                path
                for path in output_dir.iterdir()
                if path.is_file() and path.suffix.lower() in {".txt", ".srt", ".vtt"}
            )
        for path in candidates:
            try:
                with path.open("r", encoding="utf-8", errors="replace") as output:
                    return output.read(self.settings.max_output_chars + 1).strip()
            except OSError:
                continue
        return stdout[: self.settings.max_output_chars + 1].strip()


__all__ = ["LocalTranscriptionService"]
