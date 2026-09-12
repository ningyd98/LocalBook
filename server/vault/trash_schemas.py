"""DTOs for the Vault trash (soft delete with a retention window).

Naming mirrors the Vault core: every mutation answers with the trashed entry so
the client can render the recycle bin without a second round trip.  Paths are
always root-relative POSIX strings; the original location is kept for restore
and for display, never an absolute path.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from .schemas import RelativePath, Sha256

# One removable row: a file or a whole folder tree.
TrashKind = Literal["file", "directory"]
# An opaque id chosen by the service (never a path the client can influence).
TrashId = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]


class TrashEntryResponse(BaseModel):
    """One item currently held by the trash."""

    model_config = ConfigDict(extra="forbid")

    id: TrashId
    # Where the item came from; restore puts it back exactly here.
    original_path: RelativePath
    name: str
    kind: TrashKind
    # Total bytes of the trashed payload (recursive for a folder).
    byte_length: int = Field(ge=0)
    # File count for a folder, 1 for a file: what a restore brings back.
    file_count: int = Field(ge=0)
    deleted_at: datetime
    expires_at: datetime
    days_remaining: int = Field(ge=0)


class TrashListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entries: list[TrashEntryResponse]
    count: int = Field(ge=0)
    total_bytes: int = Field(ge=0)
    retention_days: int = Field(ge=1)
    generated_at: datetime


class TrashRequest(BaseModel):
    """Move one file or folder into the trash.

    ``expected_sha256`` is required for files and ignored for folders, mirroring
    the no-surprise rule of the file endpoints: a file that changed on disk after
    the user confirmed is a 409, not a silent removal.
    """

    model_config = ConfigDict(extra="forbid")

    path: RelativePath
    expected_sha256: Sha256 | None = None


class TrashRestoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # When the original path is occupied, restore as `name (restored).md`.
    rename_if_occupied: bool = False


__all__ = [
    "TrashEntryResponse",
    "TrashId",
    "TrashKind",
    "TrashListResponse",
    "TrashRequest",
    "TrashRestoreRequest",
]
