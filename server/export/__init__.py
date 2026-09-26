"""Note export: turn one Markdown note into a portable, self-contained file.

Two products share a single resolver:

* **JSON manifest** (``GET /api/v1/export/note``) — the note with every
  resolvable reference rewritten to a Vault-relative URL, plus the matching
  ``data:`` URI per attachment. The web app feeds that to the Markdown
  renderer and prints the result, so the "PDF" is produced by the browser and
  needs no server-side renderer.
* **``.md`` download** (``GET /api/v1/export/markdown``) — the same note with
  the attachments inlined as ``data:`` URIs, so one file renders everywhere.

Nothing here writes to the Vault: every read goes through ``VaultService``.
"""

from .errors import ExportError, NoteNotMarkdown, NoteNotUtf8
from .schemas import (
    ExportAttachment,
    ExportNoteResponse,
    ExportWarning,
)
from .service import EXPORT_SERVICE_VERSION, ExportResult, ExportService

__all__ = [
    "EXPORT_SERVICE_VERSION",
    "ExportAttachment",
    "ExportError",
    "ExportNoteResponse",
    "ExportResult",
    "ExportService",
    "ExportWarning",
    "NoteNotMarkdown",
    "NoteNotUtf8",
]
