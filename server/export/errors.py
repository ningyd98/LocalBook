"""Stable export error types.

Export failures are user-facing and therefore must stay explainable: a note
that is not Markdown or is not UTF-8 is a ``400`` with a fixed code, never a
stack trace. Vault-level failures (missing file, symlink, size) keep their own
``VaultError`` mapping and propagate untouched.
"""

from __future__ import annotations


class ExportError(Exception):
    """Base class for export domain errors."""

    code = "export_error"
    status_code = 400

    def __init__(self, message: str, *, code: str | None = None, path: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.path = path
        if code:
            self.code = code


class NoteNotMarkdown(ExportError):
    """The requested path is not a Markdown note (nothing to export)."""

    code = "note_not_markdown"

    def __init__(self, path: str) -> None:
        super().__init__("Only Markdown notes can be exported", path=path)


class NoteNotUtf8(ExportError):
    """The note is not valid UTF-8, so it cannot be rewritten safely."""

    code = "note_not_utf8"

    def __init__(self, path: str) -> None:
        super().__init__("The note is not valid UTF-8 and cannot be exported", path=path)


__all__ = ["ExportError", "NoteNotMarkdown", "NoteNotUtf8"]
