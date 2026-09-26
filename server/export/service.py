"""Resolve a note's references and inline its attachments.

The scanner is deliberately conservative:

* fenced code blocks and inline code are never rewritten (the shared
  ``_code_ranges`` definition from ``server.markdown.wikilinks`` is reused so
  the two modules cannot drift);
* external URLs (any ``scheme:``), protocol-relative URLs, and pure fragments
  are left exactly as authored;
* a reference is only rewritten when it resolves to an existing regular file
  *inside* the Vault — a ``..``-escape or a dangling link is reported as a
  warning and keeps its original text;
* size caps degrade (skip that attachment, keep the original reference)
  instead of failing the whole export.
"""

from __future__ import annotations

import base64
import mimetypes
import posixpath
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import quote, unquote

from ..markdown.wikilinks import _code_ranges, is_markdown_suffix
from ..vault.errors import VaultError
from ..vault.service import VaultService
from .errors import NoteNotMarkdown, NoteNotUtf8

EXPORT_SERVICE_VERSION = "export@1"

#: Default caps. Overridable per instance (``LOCALNOTE_EXPORT__*``).
DEFAULT_MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
DEFAULT_MAX_TOTAL_BYTES = 32 * 1024 * 1024

# ``![alt](dest "title")`` and ``[text](dest)``. A destination may be wrapped
# in angle brackets, which is the only way to write a path containing spaces.
_LINK_RE = re.compile(
    r"(?P<bang>!?)\[(?P<text>[^\]\n]*)\]\("
    r"\s*(?P<dest><[^<>\n]*>|[^()\s]*)"
    r"(?P<title>\s+(?:\"[^\"\n]*\"|'[^'\n]*'|\([^()\n]*\)))?"
    r"\s*\)"
)
# ``[[target]]`` / ``[[target|display]]`` / ``![[target]]``
_WIKILINK_RE = re.compile(r"(?P<bang>!?)\[\[(?P<inner>[^\[\]\n]+)\]\]")
# Raw HTML ``<img src="...">`` (rehype-raw keeps inline HTML alive in previews).
_HTML_IMG_RE = re.compile(
    r"(?P<open><img\b[^>]*?\bsrc\s*=\s*)(?P<quote>[\"'])(?P<url>[^\"']*)(?P=quote)",
    re.IGNORECASE,
)

_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")


@dataclass(slots=True)
class _Reference:
    start: int
    end: int
    kind: str  # "image" | "link" | "embed" | "wikilink" | "html"
    target: str
    text: str = ""
    title: str = ""
    raw: str = ""


@dataclass(slots=True)
class Attachment:
    ref: str
    url: str
    path: str
    mime: str
    size: int
    data_uri: str | None = None
    inlined: bool = False
    reason: str | None = None


@dataclass(slots=True)
class ExportResult:
    path: str
    title: str
    download_name: str
    #: References rewritten to Vault-relative URLs (used for rendering/print).
    markdown: str
    #: References rewritten to ``data:`` URIs (used for the ``.md`` download).
    inlined_markdown: str
    attachments: list[Attachment] = field(default_factory=list)
    warnings: list[dict[str, str]] = field(default_factory=list)
    inlined_bytes: int = 0
    truncated: bool = False
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def bytes(self) -> int:
        return len(self.inlined_markdown.encode("utf-8"))


def _mime_for(path: str) -> str:
    """Media type used inside a ``data:`` URI.

    Unknown extensions fall back to ``application/octet-stream``; ``text/*``
    and ``image/svg+xml`` are emitted with the ``;base64`` payload form too, so
    the URI never depends on the surrounding quoting rules.
    """
    guessed = mimetypes.guess_type(path)[0]
    if guessed is None:
        return "application/octet-stream"
    return guessed


def _is_external(target: str) -> bool:
    return bool(_SCHEME_RE.match(target)) or target.startswith("//")


class ExportService:
    """Read one note and produce portable Markdown for it."""

    def __init__(
        self,
        vault: VaultService,
        *,
        max_attachment_bytes: int = DEFAULT_MAX_ATTACHMENT_BYTES,
        max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
    ) -> None:
        self.vault = vault
        self.max_attachment_bytes = max(0, int(max_attachment_bytes))
        self.max_total_bytes = max(0, int(max_total_bytes))

    # -- public API ---------------------------------------------------------

    def prepare(self, note_path: str) -> ExportResult:
        """Resolve every reference of ``note_path`` and inline what fits."""
        relative = self._require_markdown(note_path)
        data, _digest = self.vault.read_bytes(relative)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as error:  # pragma: no cover - defensive
            raise NoteNotUtf8(relative) from error

        listing = self._listing()
        edits: list[tuple[int, int, str, str]] = []
        attachments: list[Attachment] = []
        warnings: list[dict[str, str]] = []
        seen: dict[str, Attachment] = {}
        inlined_bytes = 0
        truncated = False

        for reference in self._scan(text):
            raw_target = reference.target.strip()
            if not raw_target or _is_external(raw_target):
                continue

            path, reason = self._resolve(
                relative, raw_target, listing, wikilink=reference.kind in {"embed", "wikilink"}
            )
            if path is None:
                if reference.kind in {"embed", "wikilink"}:
                    # A plain ``[[Note]]`` is a note link, not an attachment:
                    # leaving it alone is correct, not a failure.
                    if reference.kind == "wikilink":
                        continue
                    warnings.append(_warning(reason or "attachment_not_found", raw_target))
                elif reason:
                    warnings.append(_warning(reason, raw_target))
                continue

            size = listing[path]
            url = quote(path, safe="/")
            existing = seen.get(path)
            if existing is not None:
                # Same file referenced twice: reuse the URI, count the bytes once.
                attachment = existing
            else:
                attachment = Attachment(
                    ref=raw_target, url=url, path=path, mime=_mime_for(path), size=size
                )
                seen[path] = attachment
                attachments.append(attachment)

            if not self._should_inline(attachment, inlined_bytes):
                truncated = True
                continue

            if attachment.data_uri is None:
                try:
                    payload, _ = self.vault.read_bytes(path)
                except VaultError:
                    warnings.append(_warning("attachment_unreadable", raw_target))
                    if existing is None:
                        attachments.remove(attachment)
                        seen.pop(path, None)
                    continue
                if len(payload) > self.max_attachment_bytes:
                    attachment.reason = "attachment_too_large"
                    truncated = True
                    continue
                attachment.data_uri = _data_uri(attachment.mime, payload)
                if existing is None:
                    inlined_bytes += len(payload)

            attachment.inlined = True
            attachment.reason = None
            edits.append(
                (
                    reference.start,
                    reference.end,
                    _render(reference, url),
                    _render(reference, attachment.data_uri or url),
                )
            )

        return ExportResult(
            path=relative,
            title=_title_of(relative),
            download_name=posixpath.basename(relative),
            markdown=_apply(text, edits, 0),
            inlined_markdown=_apply(text, edits, 1),
            attachments=attachments,
            warnings=warnings,
            inlined_bytes=inlined_bytes,
            truncated=truncated,
        )

    # -- internals ----------------------------------------------------------

    def _require_markdown(self, note_path: str) -> str:
        relative = str(note_path or "").strip()
        if not relative or not is_markdown_suffix(relative):
            raise NoteNotMarkdown(relative)
        return relative

    def _should_inline(self, attachment: Attachment, inlined_bytes: int) -> bool:
        if attachment.data_uri is not None:
            return True
        if attachment.size > self.max_attachment_bytes:
            attachment.reason = "attachment_too_large"
            return False
        if inlined_bytes + attachment.size > self.max_total_bytes:
            attachment.reason = "export_size_limit"
            return False
        return True

    def _listing(self) -> dict[str, int]:
        """Vault-relative path → byte size for every visible regular file."""
        entries = self.vault.list_files(recursive=True)
        return {entry.path: int(entry.size or 0) for entry in entries if entry.kind == "file"}

    def _scan(self, text: str) -> list[_Reference]:
        found: list[_Reference] = []
        for match in _LINK_RE.finditer(text):
            dest = match.group("dest") or ""
            target = dest[1:-1] if dest.startswith("<") and dest.endswith(">") else dest
            found.append(
                _Reference(
                    start=match.start(),
                    end=match.end(),
                    kind="image" if match.group("bang") else "link",
                    target=target,
                    text=match.group("text") or "",
                    title=match.group("title") or "",
                    raw=match.group(0),
                )
            )
        for match in _WIKILINK_RE.finditer(text):
            inner = match.group("inner")
            target, _, display = inner.partition("|")
            target = target.split("#", 1)[0].strip()
            found.append(
                _Reference(
                    start=match.start(),
                    end=match.end(),
                    kind="embed" if match.group("bang") else "wikilink",
                    target=target,
                    text=(display or "").strip(),
                    raw=match.group(0),
                )
            )
        for match in _HTML_IMG_RE.finditer(text):
            found.append(
                _Reference(
                    start=match.start(),
                    end=match.end(),
                    kind="html",
                    target=match.group("url") or "",
                    raw=match.group(0),
                )
            )

        skipped = _code_ranges(text)
        kept: list[_Reference] = []
        cursor = -1
        for reference in sorted(found, key=lambda item: (item.start, item.end)):
            if reference.start < cursor:
                continue  # overlapping match from a different pattern
            if any(start <= reference.start < end for start, end in skipped):
                continue
            kept.append(reference)
            cursor = reference.end
        return kept

    def _resolve(
        self,
        note_path: str,
        target: str,
        listing: dict[str, int],
        *,
        wikilink: bool,
    ) -> tuple[str | None, str | None]:
        """Return ``(vault_relative_path, reason)``; ``path`` is ``None`` on failure."""
        cleaned = unquote(target).split("#", 1)[0].split("?", 1)[0].strip()
        if not cleaned:
            return None, None
        if cleaned.startswith("/"):
            candidates = [cleaned.lstrip("/")]
        else:
            candidates = []
            if wikilink:
                # Obsidian resolves wiki-links Vault-wide first.
                candidates.append(cleaned)
            base = posixpath.dirname(note_path)
            joined = posixpath.join(base, cleaned) if base else cleaned
            candidates.append(posixpath.normpath(joined))

        for candidate in candidates:
            normalized = candidate.replace("\\", "/").lstrip("/")
            if (
                not normalized
                or normalized == "."
                or normalized.startswith("../")
                or "/../" in normalized
            ):
                continue
            if normalized in listing:
                return normalized, None

        if wikilink:
            resolved = self._by_basename(cleaned, listing)
            if resolved is not None:
                return resolved, None
        return None, "attachment_not_found"

    def _by_basename(self, target: str, listing: dict[str, int]) -> str | None:
        """Unique-basename lookup, matching the index's wiki-link rule."""
        name = posixpath.basename(target.replace("\\", "/")).casefold()
        if not name:
            return None
        matches = [path for path in listing if posixpath.basename(path).casefold() == name]
        if len(matches) == 1:
            return matches[0]
        return None


def _render(reference: _Reference, url: str) -> str:
    """Markdown for one reference with ``url`` substituted."""
    if reference.kind == "html":
        return reference.raw.replace(reference.target, url) if reference.target else reference.raw
    if reference.kind in {"image", "link"}:
        bang = "!" if reference.kind == "image" else ""
        return f"{bang}[{reference.text}](<{url}>{reference.title})"
    display = reference.text or posixpath.basename(reference.target)
    if reference.kind == "embed":
        return f"![{display}](<{url}>)"
    return f"[{display}](<{url}>)"


def _apply(text: str, edits: list[tuple[int, int, str, str]], index: int) -> str:
    if not edits:
        return text
    pieces: list[str] = []
    cursor = 0
    for start, end, url_form, inline_form in edits:
        pieces.append(text[cursor:start])
        pieces.append(url_form if index == 0 else inline_form)
        cursor = end
    pieces.append(text[cursor:])
    return "".join(pieces)


def _data_uri(mime: str, payload: bytes) -> str:
    encoded = base64.b64encode(payload).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _title_of(relative_path: str) -> str:
    name = posixpath.basename(relative_path)
    for suffix in (".markdown", ".md"):
        if name.casefold().endswith(suffix):
            return name[: -len(suffix)]
    return name


def _warning(code: str, ref: str) -> dict[str, str]:
    messages = {
        "attachment_not_found": "Referenced attachment was not found in the Vault",
        "attachment_too_large": "Attachment is larger than the inline size limit",
        "export_size_limit": "Attachment was skipped because the export size limit was reached",
        "attachment_unreadable": "Attachment could not be read",
    }
    return {"code": code, "ref": ref, "message": messages.get(code, "Attachment was not embedded")}


__all__ = [
    "DEFAULT_MAX_ATTACHMENT_BYTES",
    "DEFAULT_MAX_TOTAL_BYTES",
    "EXPORT_SERVICE_VERSION",
    "Attachment",
    "ExportResult",
    "ExportService",
]
