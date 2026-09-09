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


class DirectoryCreateRequest(BaseModel):
    """Create a directory (parents must already exist, like file creation)."""

    model_config = ConfigDict(extra="forbid")

    path: RelativePath


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


# ---------------------------------------------------------------------------
# Attachment uploads (PLAN-ATTACHMENTS v1.1)
# ---------------------------------------------------------------------------

_MAX_ORIGINAL_NAME_LENGTH = 1024


def normalize_target_directory_field(value: object) -> str:
    """Validate the ``target_directory`` wire field.

    ``""`` (or ``"."``) means the Vault root.  Anything else must be a
    root-relative POSIX directory path with no hidden segment and no
    ``.localnote`` segment; the service still checks that it exists and is a
    real (non-symlink) directory.
    """
    from .attachments import normalize_target_directory

    return normalize_target_directory(value)


def _validate_original_name(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("original_name must be a string")
    stripped = value.strip()
    if not stripped:
        raise ValueError("original_name must not be empty")
    if len(stripped) > _MAX_ORIGINAL_NAME_LENGTH:
        raise ValueError("original_name is too long")
    return stripped


TargetDirectory = Annotated[str, BeforeValidator(normalize_target_directory_field)]
OriginalName = Annotated[str, BeforeValidator(_validate_original_name)]


class AttachmentUploadRequest(BaseModel):
    """JSON-channel attachment upload (≤ the configured JSON threshold)."""

    model_config = ConfigDict(extra="forbid")

    original_name: OriginalName
    target_directory: TargetDirectory
    content_base64: Base64Bytes


class AttachmentUploadResponse(BaseModel):
    """Actual landing point and content metadata for one uploaded attachment."""

    model_config = ConfigDict(extra="forbid")

    path: str
    sha256: str
    byte_length: int
    content_type: str
    operation: Literal["created"]
    original_name: str


__all__ = [
    "AttachmentUploadRequest",
    "AttachmentUploadResponse",
    "Base64Bytes",
    "FileCreateRequest",
    "FileDeleteRequest",
    "FileListQuery",
    "FileMoveRequest",
    "FileMutationResponse",
    "FileReadResponse",
    "FileWriteRequest",
    "OriginalName",
    "RelativePath",
    "Sha256",
    "TargetDirectory",
    "VaultFileEntry",
    "VaultFileTreeResponse",
    "decode_base64",
    "normalize_relative_path",
    "normalize_sha256",
    "normalize_target_directory_field",
]
