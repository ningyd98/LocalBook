"""Vault trash: soft delete with a retention window (default 30 days).

**Why this module owns its own file I/O.** The trash payload lives in the
service-owned ``.localnote/trash/`` directory, and every public Vault path is
rejected there by ``is_reserved_derived_path`` on purpose (``.localnote`` is not
readable or writable through the file API).  A soft delete therefore cannot be
expressed as a public ``move_file`` call: it is an internal, service-trusted
move between two directories this module resolves itself.  The safety rules it
must keep are narrower but real:

- the source is validated exactly like a public delete (lexical validation, the
  reserved-path check, the no-symlink chain and the containment check);
- the destination is built from a generated id, never from client input, so no
  path can be authored into the trash;
- nothing is ever overwritten (a fresh id directory per entry);
- the item is *moved*, not copied: same filesystem, no byte duplication, and a
  failed move leaves both sides untouched.

**Index.** ``.localnote/trash/index.json`` is a small atomic-write JSON file.
It is derived data in exactly the M1 sense: deleting it (or the whole
``.localnote``) only loses the trash bookkeeping, never note content.  The same
holds for the payload: dropping the trash directory frees space, it never
touches the Vault tree because the payload is already outside it.

**Retention.** ``purge_expired()`` removes entries older than the configured
window and is called on every list/mutate so the promise "30 days" holds even in
a long-lived process; ``trash_retention_days`` comes from settings.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import stat
import uuid
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .derived import derived_path
from .errors import (
    AlreadyExists,
    AtomicWriteError,
    ExpectedHashRequired,
    FileConflict,
    InvalidOperation,
    NotADirectory,
    PathNotFound,
    VaultUnavailable,
)
from .path_safety import is_reserved_derived_path, validate_relative_path
from .service import VaultService

TRASH_DIR_NAME = "trash"
INDEX_FILE_NAME = "index.json"
INDEX_VERSION = 1
DEFAULT_RETENTION_DAYS = 30

# A restore name that collided gets this suffix, the same idea as the
# attachment dedup rule.
_RESTORED_SUFFIX = " (restored)"
_MAX_DEDUP_ATTEMPTS = 500


@dataclass(frozen=True, slots=True)
class TrashItem:
    """One trashed payload: its blob on disk plus where it came from."""

    id: str
    original_path: str
    kind: str  # "file" | "directory"
    byte_length: int
    file_count: int
    deleted_at: datetime
    blob: str  # trash-relative POSIX path of the payload

    @property
    def name(self) -> str:
        return self.original_path.rsplit("/", 1)[-1]

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "original_path": self.original_path,
            "kind": self.kind,
            "byte_length": self.byte_length,
            "file_count": self.file_count,
            "deleted_at": self.deleted_at.isoformat(),
            "blob": self.blob,
        }

    @classmethod
    def from_dict(cls, raw: object) -> TrashItem | None:
        if not isinstance(raw, dict):
            return None
        try:
            deleted_at = datetime.fromisoformat(str(raw["deleted_at"]))
        except (KeyError, ValueError):
            return None
        if deleted_at.tzinfo is None:
            deleted_at = deleted_at.replace(tzinfo=UTC)
        identifier = str(raw.get("id", ""))
        original = str(raw.get("original_path", ""))
        blob = str(raw.get("blob", ""))
        kind = str(raw.get("kind", "file"))
        if not identifier or not original or not blob or kind not in {"file", "directory"}:
            return None
        # A hand-edited index must not be able to point at an arbitrary location.
        if blob.startswith("/") or ".." in blob.split("/") or original.startswith("/"):
            return None
        return cls(
            id=identifier,
            original_path=original,
            kind=kind,
            byte_length=int(raw.get("byte_length") or 0),
            file_count=int(raw.get("file_count") or 0),
            deleted_at=deleted_at,
            blob=blob,
        )


@dataclass(frozen=True, slots=True)
class TrashView:
    """A trashed item plus the derived retention fields the API reports."""

    item: TrashItem
    expires_at: datetime
    days_remaining: int


class TrashService:
    """Soft delete, restore and retention for one Vault."""

    def __init__(self, vault: VaultService, *, retention_days: int = DEFAULT_RETENTION_DAYS) -> None:
        if retention_days < 1:
            raise ValueError("retention_days must be at least 1")
        self._vault = vault
        self.retention_days = retention_days

    @property
    def vault(self) -> VaultService:
        """The Vault this bin belongs to (a switch invalidates the bin)."""
        return self._vault

    # -- layout -------------------------------------------------------------
    @property
    def root(self) -> Path:
        return derived_path(self._vault.safety.root) / TRASH_DIR_NAME

    @property
    def index_path(self) -> Path:
        return self.root / INDEX_FILE_NAME

    def _ensure_root(self) -> Path:
        """Create the trash directory on demand (memory-only Vaults in tests)."""
        with self._vault._mutation_lock:  # noqa: SLF001 - same package, one lock per Vault
            root = self.root
            try:
                info = root.lstat()
            except FileNotFoundError:
                try:
                    root.mkdir(mode=0o700)
                except FileExistsError:
                    pass
                except OSError as exc:
                    raise VaultUnavailable("Vault trash cannot be initialized") from exc
                info = root.lstat()
            except OSError as exc:
                raise VaultUnavailable("Vault trash cannot be inspected") from exc
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise VaultUnavailable("Vault trash is not a directory")
            return root

    # -- index --------------------------------------------------------------
    def _load_index(self) -> list[TrashItem]:
        try:
            raw = self.index_path.read_bytes()
        except FileNotFoundError:
            return []
        except OSError as exc:
            raise VaultUnavailable("Vault trash index cannot be read") from exc
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            # A corrupt index must never block the app: the payload stays on
            # disk (nothing is deleted), it simply stops being listed.
            return []
        entries = payload.get("entries") if isinstance(payload, dict) else None
        items: list[TrashItem] = []
        for raw_entry in entries or []:
            item = TrashItem.from_dict(raw_entry)
            if item is not None:
                items.append(item)
        return items

    def _write_index(self, items: list[TrashItem]) -> None:
        root = self._ensure_root()
        payload = json.dumps(
            {"version": INDEX_VERSION, "entries": [item.to_dict() for item in items]},
            sort_keys=True,
            indent=1,
        ).encode("utf-8")
        temp = root / f".{INDEX_FILE_NAME}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        try:
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(fd, "wb") as stream:
                    fd = -1
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp, self.index_path)
            finally:
                if fd >= 0:
                    os.close(fd)
                with suppress(OSError):
                    temp.unlink(missing_ok=True)
        except OSError as exc:
            raise AtomicWriteError("Vault trash index could not be written") from exc

    # -- measurement --------------------------------------------------------
    def _measure(self, path: Path) -> tuple[int, int]:
        """``(total_bytes, file_count)`` of a file or folder tree.

        Folders are walked without following symlinks (the Vault rejects them,
        but the trash must not be a way to walk outside either).
        """
        try:
            info = path.lstat()
        except OSError as exc:
            raise VaultUnavailable("Vault trash payload cannot be inspected") from exc
        if stat.S_ISREG(info.st_mode):
            return info.st_size, 1
        if not stat.S_ISDIR(info.st_mode):
            raise NotADirectory("Trash payload is not a file or directory")
        total = 0
        files = 0
        for base, _dirs, names in os.walk(path, followlinks=False):
            for name in names:
                try:
                    child = Path(base, name).lstat()
                except OSError:
                    continue
                if stat.S_ISREG(child.st_mode):
                    total += child.st_size
                    files += 1
        return total, files

    def _blob_path(self, item: TrashItem) -> Path:
        """Resolve an index blob inside the trash, refusing anything outside."""
        root = self.root
        candidate = root.joinpath(*item.blob.split("/"))
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise InvalidOperation("Trash payload is outside the trash") from exc
        return candidate

    def _expires_at(self, item: TrashItem) -> datetime:
        return item.deleted_at + timedelta(days=self.retention_days)

    def view(self, item: TrashItem) -> TrashView:
        """Retention view: days left, rounded up so day one reads "30 days"."""
        expires = self._expires_at(item)
        seconds = (expires - datetime.now(UTC)).total_seconds()
        return TrashView(item=item, expires_at=expires, days_remaining=0 if seconds <= 0 else math.ceil(seconds / 86400))

    # -- operations ---------------------------------------------------------
    def trash(self, relative_path: str, expected_sha256: str | None) -> TrashItem:
        """Move one file or folder into the trash, then record it."""
        relative = self._vault._validate_public_path(relative_path)  # noqa: SLF001 - shared rule
        vault = self._vault
        with vault._mutation_lock:  # noqa: SLF001 - one mutation at a time per Vault
            self.purge_expired()
            source = vault.safety.resolve(relative, allow_missing=False)
            vault.safety.assert_safe_existing(source, relative_path=relative)
            info = source.lstat()
            if stat.S_ISLNK(info.st_mode):  # pragma: no cover - assert_safe_existing already rejects
                raise InvalidOperation("Symbolic links cannot be trashed")
            if stat.S_ISREG(info.st_mode):
                if not expected_sha256:
                    raise ExpectedHashRequired(path=relative)
                _data, digest = vault._snapshot(relative, source)  # noqa: SLF001 - reuse hash guard
                if digest != expected_sha256:
                    raise FileConflict(path=relative)
                kind = "file"
            elif stat.S_ISDIR(info.st_mode):
                kind = "directory"
            else:
                raise NotADirectory("Only files and folders can be trashed", path=relative)

            root = self._ensure_root()
            identifier = uuid.uuid4().hex
            name = relative.rsplit("/", 1)[-1]
            holder = root / identifier
            try:
                holder.mkdir(mode=0o700)
            except OSError as exc:
                raise AtomicWriteError("Vault trash entry could not be created") from exc
            destination = holder / name
            try:
                shutil.move(str(source), str(destination))
            except OSError as exc:
                with suppress(OSError):
                    shutil.rmtree(holder, ignore_errors=True)
                raise AtomicWriteError("Vault item could not be moved to the trash") from exc

            byte_length, file_count = self._measure(destination)
            item = TrashItem(
                id=identifier,
                original_path=relative,
                kind=kind,
                byte_length=byte_length,
                file_count=file_count,
                deleted_at=datetime.now(UTC),
                blob=f"{identifier}/{name}",
            )
            items = self._load_index()
            items.append(item)
            self._write_index(items)
            return item

    def list_entries(self) -> list[TrashView]:
        with self._vault._mutation_lock:  # noqa: SLF001
            self.purge_expired()
            views = [self.view(item) for item in self._load_index()]
        views.sort(key=lambda view: view.item.deleted_at, reverse=True)
        return views

    def _entry(self, entry_id: str) -> tuple[list[TrashItem], TrashItem]:
        items = self._load_index()
        for index, item in enumerate(items):
            if item.id == entry_id:
                return items, item
        raise PathNotFound("Trash entry was not found")

    def restore(self, entry_id: str, *, rename_if_occupied: bool = False) -> tuple[str, bool]:
        """Move an entry back to its original path.

        Returns ``(restored_path, renamed)``.  A missing parent folder is
        recreated (the folder may have been trashed separately); an occupied
        path is a 409 unless ``rename_if_occupied`` asked for a fresh name.
        """
        vault = self._vault
        with vault._mutation_lock:  # noqa: SLF001
            self.purge_expired()
            items, item = self._entry(entry_id)
            blob = self._blob_path(item)
            vault.safety.assert_safe_existing(blob, relative_path=item.blob)

            target_relative = item.original_path
            target = vault.safety.resolve(target_relative, allow_missing=True)
            if target.exists() or target.is_symlink():
                if not rename_if_occupied:
                    raise AlreadyExists("The original path is occupied", path=target_relative)
                target_relative = self._free_name(target_relative)
                target = vault.safety.resolve(target_relative, allow_missing=True)

            parent = target.parent
            if not parent.exists():
                try:
                    parent.mkdir(parents=True, exist_ok=True)
                except OSError as exc:
                    raise VaultUnavailable("Vault folder could not be recreated") from exc
            vault.safety.assert_safe_existing(parent, relative_path=vault.safety.display_path(parent))
            try:
                shutil.move(str(blob), str(target))
            except OSError as exc:
                raise AtomicWriteError("Trash entry could not be restored") from exc

            holder = blob.parent
            with suppress(OSError):
                holder.rmdir()
            remaining = [entry for entry in items if entry.id != item.id]
            self._write_index(remaining)
            return target_relative, target_relative != item.original_path

    def _free_name(self, relative_path: str) -> str:
        directory, _, name = relative_path.rpartition("/")
        stem, dot, extension = name.rpartition(".")
        if not dot:
            stem, extension = name, ""
        for attempt in range(_MAX_DEDUP_ATTEMPTS):
            suffix = _RESTORED_SUFFIX if attempt == 0 else f"{_RESTORED_SUFFIX} {attempt + 1}"
            candidate = f"{stem}{suffix}{dot}{extension}" if dot else f"{stem}{suffix}"
            candidate_relative = f"{directory}/{candidate}" if directory else candidate
            try:
                resolved = self._vault.safety.resolve(candidate_relative, allow_missing=True)
            except Exception as exc:  # noqa: BLE001 - an unusable candidate is skipped
                raise InvalidOperation("Restore target is not a safe path") from exc
            if not resolved.exists() and not resolved.is_symlink():
                return candidate_relative
        raise AlreadyExists("No free name is available for the restore", path=relative_path)

    def delete(self, entry_id: str) -> str:
        """Remove one entry permanently (the payload, then the index record)."""
        with self._vault._mutation_lock:  # noqa: SLF001
            items, item = self._entry(entry_id)
            self._remove_payload(item)
            self._write_index([entry for entry in items if entry.id != item.id])
            return item.original_path

    def empty(self) -> int:
        """Permanently remove every entry; returns how many were removed."""
        with self._vault._mutation_lock:  # noqa: SLF001
            items = self._load_index()
            for item in items:
                self._remove_payload(item)
            if items:
                self._write_index([])
            return len(items)

    def purge_expired(self, *, now: datetime | None = None) -> int:
        """Drop entries past the retention window; returns how many went."""
        moment = now or datetime.now(UTC)
        with self._vault._mutation_lock:  # noqa: SLF001
            items = self._load_index()
            if not items:
                return 0
            kept: list[TrashItem] = []
            expired: list[TrashItem] = []
            for item in items:
                (expired if self._expires_at(item) <= moment else kept).append(item)
            if not expired:
                return 0
            for item in expired:
                self._remove_payload(item)
            self._write_index(kept)
            return len(expired)

    def _remove_payload(self, item: TrashItem) -> None:
        """Delete a payload; a payload that is already gone is not an error."""
        root = self.root
        holder = root / item.id
        try:
            holder.relative_to(root)
        except ValueError as exc:
            raise InvalidOperation("Trash payload is outside the trash") from exc
        try:
            info = holder.lstat()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise VaultUnavailable("Vault trash entry cannot be inspected") from exc
        if stat.S_ISLNK(info.st_mode):
            raise InvalidOperation("Trash entry must not be a symbolic link")
        try:
            if stat.S_ISDIR(info.st_mode):
                shutil.rmtree(holder)
            else:
                holder.unlink()
        except OSError as exc:
            raise AtomicWriteError("Trash entry could not be removed") from exc


def validate_trash_id(value: object) -> str:
    """Lexical check for an entry id (ids are generated, never authored)."""
    if not isinstance(value, str) or not value or len(value) > 64:
        raise InvalidOperation("Trash entry id is not valid")
    if not all(character.isalnum() or character in "_-" for character in value):
        raise InvalidOperation("Trash entry id is not valid")
    # An id is a single trash-relative component, never a path.
    if validate_relative_path(value) != value:
        raise InvalidOperation("Trash entry id is not valid")
    if is_reserved_derived_path(value):  # pragma: no cover - ids are alphanumeric
        raise InvalidOperation("Trash entry id is not valid")
    return value


__all__ = [
    "DEFAULT_RETENTION_DAYS",
    "INDEX_FILE_NAME",
    "TRASH_DIR_NAME",
    "TrashItem",
    "TrashService",
    "TrashView",
    "validate_trash_id",
]
