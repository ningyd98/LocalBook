"""Raw Markdown/attachment bytes boundary for M1.

This module intentionally does not parse Markdown, frontmatter, links, or any
other syntax.  Callers receive exactly the bytes stored in the Vault and hash
those bytes with SHA-256.  File I/O remains in ``VaultService``.
"""

from __future__ import annotations

import hashlib


def sha256_bytes(data: bytes) -> str:
    """Return the stable ``sha256:<lowercase-hex>`` digest for raw bytes."""
    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def byte_length(data: bytes) -> int:
    """Return the byte length without decoding or newline normalization."""
    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")
    return len(data)


def snapshot(data: bytes) -> tuple[bytes, str, int]:
    """Return ``(same_bytes, sha256, byte_length)`` for API metadata."""
    return data, sha256_bytes(data), byte_length(data)


__all__ = ["byte_length", "sha256_bytes", "snapshot"]
