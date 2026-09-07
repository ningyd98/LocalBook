"""M1 Markdown/attachment byte fidelity (§8.2): raw bytes only, no parser.

M1 must never decode, normalize newlines, strip BOMs, reformat Markdown or
infer titles.  Everything below asserts byte equality and stable SHA-256
digests over the raw file bytes.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable
from pathlib import Path

import pytest

from server.markdown.bytes import byte_length, snapshot
from server.markdown.bytes import sha256_bytes as md_sha256
from server.vault.errors import ExpectedHashRequired
from server.vault.service import VaultService, sha256_bytes

# Byte-level payloads that must round-trip exactly: newline styles, BOM,
# no trailing newline, non-UTF-8, unknown Obsidian/HTML/code syntax, emoji.
_RAW_PAYLOADS: list[tuple[str, bytes]] = [
    ("lf.md", b"# line one\nline two\n"),
    ("crlf.md", b"# CRLF title\r\n\r\nline two\r\n"),
    ("no-trailing-newline.md", b"# title\nlast line"),
    ("bom-lf.md", b"\xef\xbb\xbf# BOM title\nline\n"),
    ("non-utf8.md", b"# caf\xe9 non-utf8\r\nraw \xff\xfe bytes\r\n"),
    (
        "unknown-syntax.md",
        b"---\ntitle: Keep Me\n---\n"
        b"> [!warning] Callout\n> keep\n\n"
        b"```python\ndef x(): return 1\n```\n\n"
        b"<details><summary>HTML</summary>body</details>\n\n"
        b"![[embed.png]] and [[target|alias]] and [^1]\n\n[^1]: note\n",
    ),
    ("emoji.md", "# 🎉 Emoji 中文 😀\n".encode()),
    ("empty.md", b""),
    ("binary-attachment.bin", bytes(range(256)) * 4),
]


def test_fixture_byte_files_read_back_verbatim(
    vault_fixture_copy: Path,
    vault_service_factory: Callable[..., VaultService],
) -> None:
    root = vault_fixture_copy
    service = vault_service_factory(root)
    for path in sorted((root / "bytes").iterdir()):
        relative = f"bytes/{path.name}"
        expected = path.read_bytes()
        data, digest = service.read_bytes(relative)
        assert data == expected, f"{relative} was rewritten"
        assert digest == sha256_bytes(expected)
        assert md_sha256(expected) == digest


def test_raw_payloads_roundtrip_create_and_read(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    service = vault_service_factory(root)
    for relative, payload in _RAW_PAYLOADS:
        service.create_bytes(relative, payload)
        data, digest = service.read_bytes(relative)
        assert data == payload, f"{relative} did not round-trip"
        assert digest == sha256_bytes(payload)
        assert service.read_file(relative)["byte_length"] == len(payload)


def test_update_never_normalizes_bytes(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    service = vault_service_factory(root)
    payload = b"# \r\n weird \x00-ish content\r\nwith \r mixed endings\r\n"
    service.create_bytes("doc.md", payload)
    _data, digest = service.read_bytes("doc.md")

    # Updating with identical bytes and the correct digest must be a no-op in
    # terms of content: hash stays stable, bytes stay identical.
    result = service.write_bytes("doc.md", payload, digest)
    assert result["sha256"] == digest
    data, _ = service.read_bytes("doc.md")
    assert data == payload


def test_missing_expected_hash_rejected_on_update(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    service = vault_service_factory(root)
    service.create_bytes("doc.md", b"original\n")
    with pytest.raises(ExpectedHashRequired):
        service.write_bytes("doc.md", b"replacement\n", None)
    data, _ = service.read_bytes("doc.md")
    assert data == b"original\n"


def test_markdown_bytes_helper_functions() -> None:
    payload = b"\xef\xbb\xbf# h\r\n"
    assert byte_length(payload) == len(payload)
    digest = md_sha256(payload)
    assert digest == sha256_bytes(payload)
    assert digest == f"sha256:{__import__('hashlib').sha256(payload).hexdigest()}"
    same, hash_value, length = snapshot(payload)
    assert same is payload
    assert hash_value == digest
    assert length == len(payload)
    with pytest.raises(TypeError):
        md_sha256("not bytes")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        byte_length("not bytes")  # type: ignore[arg-type]


def test_no_implicit_utf8_decoding_happens(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    """Non-UTF-8 files are stored and returned as opaque bytes."""
    root = tmp_path / "vault"
    root.mkdir()
    service = vault_service_factory(root)
    opaque = bytes(range(1, 255))  # deliberately not valid UTF-8
    service.create_bytes("opaque.md", opaque)
    data, _ = service.read_bytes("opaque.md")
    assert data == opaque
    # Python would raise on a strict decode; bytes access never decodes.
    with pytest.raises(UnicodeDecodeError):
        data.decode("utf-8")


def test_nfd_content_and_names_untouched(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    service = vault_service_factory(root)
    content = unicodedata.normalize("NFD", "café résumé\n").encode("utf-8")
    name = unicodedata.normalize("NFD", "résumé.md")
    service.create_bytes(name, content)
    data, _ = service.read_bytes(name)
    assert data == content
