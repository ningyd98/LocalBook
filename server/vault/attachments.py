"""Attachment naming and target-directory rules (ATT-01).

This module is pure: it never touches the filesystem and never accepts a
filesystem ``Path`` from a caller.  The upload service calls these helpers to
turn an untrusted display name plus an already validated root-relative target
directory into a safe, predictable ``<target directory>/<safe basename>``
landing path.

Design notes (PLAN-ATTACHMENTS.md v1.1):

* There is no fixed ``attachments/YYYY-MM/`` layout.  The caller decides the
  target directory; this module only validates/joins it.
* Unicode NFC normalisation is used for *name comparison/display cleaning*
  only.  File content bytes are never touched or re-encoded.
* The basename keeps readable characters (Chinese, emoji), folds whitespace to
  ``-``, maps dangerous characters to ``-``, collapses repeated ``-`` and
  preserves the last ordinary extension when it is safe.
* The cleaned basename is limited to 255 UTF-8 bytes and never cuts a UTF-8
  character in half; the full root-relative path is limited to 4096 bytes.
* Name collisions are resolved by ``-2``, ``-3`` … suffixes; an optional content
  digest may supply a ``-<6 hex>`` suffix on the first attempt.  The digest is
  a naming hint only, never a substitute for content hashing.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

from .errors import InvalidRequest
from .path_safety import is_reserved_derived_path, validate_relative_path

MAX_BASENAME_BYTES = 255
MAX_RELATIVE_PATH_BYTES = 4096
DEFAULT_ATTACHMENT_BASENAME = "attachment"
DIGEST_SUFFIX_LENGTH = 6
MAX_DEDUP_ATTEMPTS = 1000

# ASCII control characters, NUL, DEL and the C1 range.
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
# Characters that are structurally unsafe in a POSIX filename, are Windows
# reserved, or would be re-interpreted by a shell/Markdown link.
_DANGEROUS_CHARS = set('/\\:*?"<>|%#$@!`\'{}[]()^&+=;,~')
# Whitespace (any Unicode whitespace) folds to a single separator.
_WHITESPACE_RE = re.compile(r"\s+")
_DASH_RUN_RE = re.compile(r"-{2,}")
# Windows device names are rejected even with an extension appended.
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
_SAFE_EXTENSION_RE = re.compile(r"^\.[0-9A-Za-z][0-9A-Za-z._-]{0,31}$")
_HEX_RE = re.compile(r"^[0-9a-f]{6,}$")


def _nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def _truncate_utf8(value: str, limit: int) -> str:
    """Truncate to at most ``limit`` UTF-8 bytes without splitting a char."""
    if limit <= 0:
        return ""
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value
    return encoded[:limit].decode("utf-8", errors="ignore")


def _clean_base(raw: str) -> str:
    """Clean a display name into a safe basename *without* its extension."""
    text = _nfc(raw)
    text = _CONTROL_RE.sub("", text)
    # Every path separator and dangerous character becomes a separator; the
    # caller's directory part is therefore impossible to inject here.
    text = "".join("-" if char in _DANGEROUS_CHARS else char for char in text)
    text = _WHITESPACE_RE.sub("-", text)
    # Leading dots/separators would create hidden names or empty segments.
    text = text.lstrip(".-")
    text = text.rstrip(".- ")
    text = _DASH_RUN_RE.sub("-", text)
    text = text.strip("-")
    if not text:
        return ""
    if text.split(".")[0].upper() in _WINDOWS_RESERVED:
        text = f"_{text}"
    return text


def _split_extension(raw: str) -> tuple[str, str]:
    """Split ``name`` into (base, extension); extension is only kept if safe."""
    text = _nfc(raw)
    text = _CONTROL_RE.sub("", text)
    text = text.replace("\\", "-").replace("/", "-")
    # A Windows device name has no usable extension semantics.
    stem = text.split(".", 1)[0].strip().upper()
    if stem in _WINDOWS_RESERVED:
        return text, ""
    index = text.rfind(".")
    if index <= 0:
        return text, ""
    extension = text[index:]
    if not _SAFE_EXTENSION_RE.fullmatch(extension):
        return text, ""
    return text[:index], extension


def clean_attachment_basename(original_name: object) -> str:
    """Return the safe basename for ``original_name`` (never empty).

    The returned value is a single path segment: no separator, no ``.``/``..``,
    no leading dot (so it can never create a hidden or reserved name), no
    trailing dot/space, and at most 255 UTF-8 bytes.
    """
    if not isinstance(original_name, str):
        raise InvalidRequest("original_name must be a string")
    raw = original_name.strip()
    if not raw:
        return DEFAULT_ATTACHMENT_BASENAME
    # Ignore any directory part a client may send; only the last segment is a
    # naming input.  Backslashes are handled by ``_split_extension``.
    raw = raw.replace("\\", "/").split("/")[-1]
    base, extension = _split_extension(raw)
    cleaned_base = _clean_base(base)
    extension = _nfc(extension)
    if not _SAFE_EXTENSION_RE.fullmatch(extension):
        extension = ""
    extension_bytes = len(extension.encode("utf-8"))
    if extension_bytes >= MAX_BASENAME_BYTES:
        # An absurdly long "extension" is not an extension.
        cleaned_base = _clean_base(base + extension)
        extension = ""
        extension_bytes = 0
    budget = MAX_BASENAME_BYTES - extension_bytes
    cleaned_base = _truncate_utf8(cleaned_base, budget).rstrip(".-")
    if not cleaned_base:
        cleaned_base = _truncate_utf8(DEFAULT_ATTACHMENT_BASENAME, budget)
    name = f"{cleaned_base}{extension}"
    if name in {".", ".."}:
        return DEFAULT_ATTACHMENT_BASENAME
    return name


def _with_suffix(basename: str, suffix: str) -> str:
    """Append ``suffix`` to a basename before its extension, keeping 255 bytes."""
    base, extension = _split_extension(basename)
    if not extension:
        base = basename
    extension_bytes = len(extension.encode("utf-8"))
    budget = max(1, MAX_BASENAME_BYTES - extension_bytes - len(suffix.encode("utf-8")))
    trimmed = _truncate_utf8(base, budget).rstrip(".-")
    if not trimmed:
        trimmed = _truncate_utf8(DEFAULT_ATTACHMENT_BASENAME, budget) or "a"
    return f"{trimmed}{suffix}{extension}"


def normalize_target_directory(target_directory: object) -> str:
    """Validate a root-relative target directory.

    Returns ``""`` for the Vault root.  Rejects absolute paths, ``.``/``..``
    segments, NUL, backslashes, drive/UNC prefixes, empty segments, hidden
    segments (any segment starting with ``.``) and ``.localnote`` segments.
    """
    if target_directory is None:
        raise InvalidRequest("target_directory must be a string")
    if not isinstance(target_directory, str):
        raise InvalidRequest("target_directory must be a string")
    if target_directory in {"", "."}:
        return ""
    relative = validate_relative_path(target_directory)
    if is_reserved_derived_path(relative):
        raise InvalidRequest("The .localnote directory is reserved", path=relative)
    for segment in relative.split("/"):
        if segment.startswith("."):
            raise InvalidRequest("Hidden directories are not writable", path=relative)
    return relative


def join_attachment_path(target_directory: str, basename: str) -> str:
    """Join an already validated directory with a cleaned basename."""
    relative = basename if not target_directory else f"{target_directory}/{basename}"
    if len(relative.encode("utf-8")) > MAX_RELATIVE_PATH_BYTES:
        raise InvalidRequest("Attachment path is too long", path=target_directory or None)
    return relative


def _existing_basenames(existing_names: Iterable[str]) -> set[str]:
    names: set[str] = set()
    for value in existing_names:
        if not isinstance(value, str):
            continue
        names.add(value.replace("\\", "/").split("/")[-1])
    return names


def _digest_suffix(content_digest: str | None) -> str:
    if not content_digest:
        return ""
    digest = content_digest.removeprefix("sha256:").lower()
    if not _HEX_RE.fullmatch(digest):
        return ""
    return f"-{digest[:DIGEST_SUFFIX_LENGTH]}"


def safe_attachment_name(
    original_name: object,
    target_directory: object = "",
    existing_names: Iterable[str] = (),
    content_digest: str | None = None,
) -> str:
    """Return the ``<target directory>/<safe basename>`` landing path.

    ``existing_names`` are the names already present in the target directory
    (basenames or full root-relative paths; only the last segment is compared).
    This is a best-effort pre-check: the atomic no-overwrite commit remains the
    only real collision guarantee.
    """
    directory = normalize_target_directory(target_directory)
    basename = clean_attachment_basename(original_name)
    taken = _existing_basenames(existing_names)

    # A digest suffix is a first-attempt naming hint only; the plain name is
    # still tried next, and numeric suffixes are the deterministic fallback.
    suffix = _digest_suffix(content_digest)
    if suffix:
        candidate = _with_suffix(basename, suffix)
        if candidate not in taken:
            return join_attachment_path(directory, candidate)

    candidate = basename
    if candidate not in taken:
        return join_attachment_path(directory, candidate)
    for counter in range(2, MAX_DEDUP_ATTEMPTS + 2):
        candidate = _with_suffix(basename, f"-{counter}")
        if candidate not in taken:
            return join_attachment_path(directory, candidate)
    raise InvalidRequest("No available attachment name", path=directory or None)


def is_image_attachment(original_name: str, content_type: str | None = None) -> bool:
    """True when the attachment should be embedded with ``![alt](path)``.

    MIME is preferred and a safe image extension is the fallback; the server
    never sniffs or validates actual image bytes.
    """
    if isinstance(content_type, str) and content_type.lower().startswith("image/"):
        return True
    lowered = original_name.lower()
    return lowered.endswith(
        (".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".bmp", ".svg", ".ico")
    )


__all__ = [
    "DEFAULT_ATTACHMENT_BASENAME",
    "DIGEST_SUFFIX_LENGTH",
    "MAX_BASENAME_BYTES",
    "MAX_DEDUP_ATTEMPTS",
    "MAX_RELATIVE_PATH_BYTES",
    "clean_attachment_basename",
    "is_image_attachment",
    "join_attachment_path",
    "normalize_target_directory",
    "safe_attachment_name",
]
