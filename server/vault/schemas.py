"""Pydantic DTOs and validated scalar types for the Vault API."""

from __future__ import annotations

import base64
import binascii
import re
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

_SHA256_RE = re.compile(r"^(?:sha256:)?[0-9a-fA-F]{64}$")
_MAX_RELATIVE_PATH_BYTES = 4096
_MAX_SEGMENT_BYTES = 255


def normalize_relative_path(value: object) -> str:
    """Validate and canonicalise an API root-relative POSIX path.

    This is lexical validation only.  ``VaultService`` must still perform the
    filesystem checks immediately before every operation.
    """
    if not isinstance(value, str):
        raise ValueError("path must be a string")
    if not value:
        raise ValueError("path must not be empty")
    if "\x00" in value:
        raise ValueError("path must not contain NUL")
    if "\\" in value:
        raise ValueError("path must use POSIX separators")
    if value.startswith("/") or value.startswith("//"):
        raise ValueError("path must be root-relative")
    if len(value.encode("utf-8")) > _MAX_RELATIVE_PATH_BYTES:
        raise ValueError("path is too long")

    segments = value.split("/")
    if any(not segment for segment in segments):
        raise ValueError("path contains an empty segment")
    for segment in segments:
        if segment in {".", ".."}:
            raise ValueError("path contains an unsafe segment")
        if len(segment.encode("utf-8")) > _MAX_SEGMENT_BYTES:
            raise ValueError("path segment is too long")

    # Purely lexical Windows drive/UNC checks keep this contract stable on
    # POSIX hosts as well as Windows/WSL.
    first = segments[0]
    if len(first) >= 2 and first[1] == ":":
        raise ValueError("path must not contain a drive prefix")
    if value.startswith("\\") or value.startswith("?"):
        raise ValueError("path must not contain a UNC/device prefix")
    return "/".join(segments)


def normalize_sha256(value: object) -> str:
    """Return the canonical ``sha256:<lowercase hex>`` representation."""
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ValueError("expected_sha256 must be a SHA-256 digest")
    digest = value.removeprefix("sha256:").lower()
    return f"sha256:{digest}"


def decode_base64(value: object) -> bytes:
    """Strictly decode a standard Base64 JSON string to raw bytes."""
    if not isinstance(value, str):
        raise ValueError("content_base64 must be a Base64 string")
    try:
        # validate=True rejects whitespace and non-alphabet characters instead
        # of silently accepting malformed content.
        return base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError, binascii.Error) as exc:
        raise ValueError("content_base64 is invalid") from exc


RelativePath = Annotated[str, BeforeValidator(normalize_relative_path)]
Sha256 = Annotated[str, BeforeValidator(normalize_sha256)]
Base64Bytes = Annotated[bytes, BeforeValidator(decode_base64)]


class VaultFileEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    kind: Literal["file", "directory"]
    size: int | None = None
    sha256: str | None = None


class VaultFileTreeResponse(BaseModel):
    root: str = "."
    entries: list[VaultFileEntry] = Field(default_factory=list)
    generated_at: datetime


class FileReadResponse(BaseModel):
    path: str
    content_base64: str
    byte_length: int
    sha256: str
    content_type: str | None = None


class FileCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: RelativePath
    content_base64: Base64Bytes = b""


class FileWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: RelativePath
    content_base64: Base64Bytes
    # Optional at the schema layer so a missing digest surfaces as the domain
    # error ``400 expected_hash_required`` (PLAN-M1 §4.4) instead of a 422.
    expected_sha256: Sha256 | None = None


class FileDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: RelativePath
    expected_sha256: Sha256 | None = None


class FileMoveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_path: RelativePath
    destination_path: RelativePath
    expected_sha256: Sha256 | None = None


class FileMutationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    sha256: str | None = None
    byte_length: int | None = None
    operation: Literal["created", "updated", "deleted", "moved"]


class FileListQuery(BaseModel):
    """Internal query DTO used by tests and callers that want validation."""

    path: str | None = None
    recursive: bool = True
    include_hidden: bool = False


__all__ = [
    "Base64Bytes",
    "FileCreateRequest",
    "FileDeleteRequest",
    "FileListQuery",
    "FileMoveRequest",
    "FileMutationResponse",
    "FileReadResponse",
    "FileWriteRequest",
    "RelativePath",
    "Sha256",
    "VaultFileEntry",
    "VaultFileTreeResponse",
    "decode_base64",
    "normalize_relative_path",
    "normalize_sha256",
]
