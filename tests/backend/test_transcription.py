"""Local audio transcription contracts."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from server.api.main import create_app
from server.config import Settings, TranscriptionSettings, VaultSettings
from server.transcription.errors import TranscriptionError, TranscriptionErrorCode
from server.transcription.service import LocalTranscriptionService
from server.vault.service import VaultService
from tests.backend.client import TestClient


def _fake_tool(tmp_path: Path) -> Path:
    script = tmp_path / "fake_transcriber.py"
    script.write_text(
        """
import argparse
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--input', required=True)
parser.add_argument('--output', required=True)
args = parser.parse_args()
assert Path(args.input).read_bytes().startswith(b'RIFF')
Path(args.output).write_text('本地转写成功\\n第二行', encoding='utf-8')
""",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | os.X_OK)
    return script


def _service(root: Path, tool: Path) -> LocalTranscriptionService:
    vault = VaultService(root, watcher_enabled=False)
    vault.initialize(start_watcher=False)
    settings = TranscriptionSettings(
        command_template=f"{sys.executable} {tool} --input {{input}} --output {{output}}",
        model="tiny",
        timeout_seconds=10,
    )
    return LocalTranscriptionService(vault, settings)


def test_default_whisper_command_uses_private_vault_model_cache(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    vault = VaultService(root, watcher_enabled=False)
    vault.initialize(start_watcher=False)
    service = LocalTranscriptionService(vault, TranscriptionSettings())

    command, _ = service._build_command(
        input_path=tmp_path / "recording.m4a",
        output_dir=tmp_path / "output",
        requested_language=None,
    )

    assert command[-2:] == ["--model_dir", str(root / ".localnote" / "whisper-models")]


def test_transcribes_audio_via_configured_local_command(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "recording.wav").write_bytes(b"RIFF" + b"\x00" * 32)
    service = _service(root, _fake_tool(tmp_path))

    result = service.transcribe("recording.wav")

    assert result == {
        "path": "recording.wav",
        "text": "本地转写成功\n第二行",
        "language": None,
        "tool": Path(sys.executable).name,
        "truncated": False,
    }


def test_rejects_non_audio_files_before_starting_tool(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "note.md").write_text("# note", encoding="utf-8")
    service = _service(root, _fake_tool(tmp_path))

    with pytest.raises(TranscriptionError) as raised:
        service.transcribe("note.md")

    assert raised.value.code is TranscriptionErrorCode.NOT_AUDIO
    assert raised.value.status_code == 400
    assert raised.value.path == "note.md"


def test_missing_local_tool_is_reported_without_exposing_absolute_paths(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "recording.mp3").write_bytes(b"audio")
    vault = VaultService(root, watcher_enabled=False)
    vault.initialize(start_watcher=False)
    service = LocalTranscriptionService(
        vault,
        TranscriptionSettings(command_template="definitely-not-installed {input}"),
    )

    with pytest.raises(TranscriptionError) as raised:
        service.transcribe("recording.mp3")

    assert raised.value.code is TranscriptionErrorCode.UNAVAILABLE
    assert str(root) not in raised.value.message


def test_transcription_route_returns_text_and_safe_error_contract(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "recording.wav").write_bytes(b"RIFF" + b"\x00" * 32)
    settings = Settings(
        vault=VaultSettings(root=root, watcher_enabled=False),
        transcription=TranscriptionSettings(
            command_template=(
                f"{sys.executable} {_fake_tool(tmp_path)} "
                "--input {input} --output {output}"
            ),
        ),
    )
    client = TestClient(create_app(settings))

    response = client.post("/api/v1/transcription", json={"path": "recording.wav"})

    assert response.status_code == 200
    assert response.json()["text"] == "本地转写成功\n第二行"
    assert response.json()["path"] == "recording.wav"
