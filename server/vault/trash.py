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
# Bound synchronous retention work on request paths; callers can invoke purge
# repeatedly (or from a maintenance task) to drain additional batches.
_DEFAULT_PURGE_BATCH_SIZE = 16
_PURGE_DIR_PREFIX = ".purge-"


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
        required = {
            "id",
            "original_path",
            "kind",
            "byte_length",
            "file_count",
            "deleted_at",
            "blob",
        }
        if not isinstance(raw, dict) or set(raw) != required:
            return None
        if not isinstance(raw.get("deleted_at"), str):
            return None
        try:
            deleted_at = datetime.fromisoformat(raw["deleted_at"])
        except ValueError:
            return None
        if deleted_at.tzinfo is None:
            deleted_at = deleted_at.replace(tzinfo=UTC)
        identifier = raw.get("id")
        original = raw.get("original_path")
        blob = raw.get("blob")
        kind = raw.get("kind")
        byte_length = raw.get("byte_length")
        file_count = raw.get("file_count")
        if not all(isinstance(value, str) for value in (identifier, original, blob, kind)):
            return None
        if not isinstance(byte_length, int) or isinstance(byte_length, bool) or byte_length < 0:
            return None
        if not isinstance(file_count, int) or isinstance(file_count, bool) or file_count < 0:
            return None
        if kind not in {"file", "directory"} or validate_trash_id(identifier) != identifier:
            return None
        try:
            original = validate_relative_path(original)
            blob = validate_relative_path(blob)
        except Exception:
            return None
        if blob.split("/", 1)[0] != identifier or len(blob.split("/")) < 2:
            return None
        return cls(
            id=identifier,
            original_path=original,
            kind=kind,
            byte_length=byte_length,
            file_count=file_count,
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

    def __init__(
        self, vault: VaultService, *, retention_days: int = DEFAULT_RETENTION_DAYS
    ) -> None:
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
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise VaultUnavailable("Vault trash index is corrupt") from exc
        valid_payload = (
            isinstance(payload, dict)
            and set(payload) == {"version", "entries"}
            and payload.get("version") == INDEX_VERSION
            and isinstance(payload.get("entries"), list)
        )
        if not valid_payload:
            raise VaultUnavailable("Vault trash index is invalid")
        entries = payload["entries"]
        items: list[TrashItem] = []
        seen: set[str] = set()
        for raw_entry in entries:
            item = TrashItem.from_dict(raw_entry)
            if item is None or item.id in seen:
                raise VaultUnavailable("Vault trash index contains an invalid entry")
            seen.add(item.id)
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
        validate_trash_id(item.id)
        try:
            blob = validate_relative_path(item.blob)
            if blob.split("/", 1)[0] != item.id:
                raise ValueError("blob id mismatch")
            candidate = root.joinpath(*blob.split("/"))
            candidate.relative_to(root)
            current = root
            for part in blob.split("/"):
                current = current / part
                try:
                    info = current.lstat()
                except FileNotFoundError:
                    break
                if stat.S_ISLNK(info.st_mode):
                    raise InvalidOperation("Trash payload must not be a symbolic link")
        except (ValueError, OSError) as exc:
            raise InvalidOperation("Trash payload is outside the trash") from exc
        return candidate

    def _expires_at(self, item: TrashItem) -> datetime:
        return item.deleted_at + timedelta(days=self.retention_days)

    def view(self, item: TrashItem) -> TrashView:
        """Retention view: days left, rounded up so day one reads "30 days"."""
        expires = self._expires_at(item)
        seconds = (expires - datetime.now(UTC)).total_seconds()
        days_remaining = 0 if seconds <= 0 else math.ceil(seconds / 86400)
        return TrashView(item=item, expires_at=expires, days_remaining=days_remaining)

    # -- operations ---------------------------------------------------------
    def trash(self, relative_path: str, expected_sha256: str | None) -> TrashItem:
        """Move one file or folder into the trash, then record it."""
        relative = self._vault._validate_public_path(relative_path)  # noqa: SLF001 - shared rule
        vault = self._vault
        with vault._mutation_lock:  # noqa: SLF001 - one mutation at a time per Vault
            self.purge_expired(limit=_DEFAULT_PURGE_BATCH_SIZE)
            source = vault.safety.resolve(relative, allow_missing=False)
            vault.safety.assert_safe_existing(source, relative_path=relative)
            info = source.lstat()
            if stat.S_ISLNK(info.st_mode):  # pragma: no cover
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
                os.rename(source, destination)
            except OSError as exc:
                with suppress(OSError):
                    shutil.rmtree(holder, ignore_errors=True)
                raise AtomicWriteError("Vault item could not be moved to the trash") from exc

            try:
                byte_length, file_count = self._measure(destination)
            except Exception:
                os.rename(destination, source)
                with suppress(OSError):
                    holder.rmdir()
                raise
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
            try:
                self._write_index(items)
            except Exception:
                # The source remains recoverable even if committing the index fails.
                with suppress(OSError):
                    os.rename(destination, source)
                    holder.rmdir()
                raise
            return item

    def list_entries(self) -> list[TrashView]:
        # Quarantine cleanup runs outside the Vault mutation lock and is bounded
        # by a small number of directory trees per request.
        # Validate the derived root before glob/rename/rmtree maintenance; the
        # path may have been replaced since this service was constructed.
        self._ensure_root()
        self._clean_quarantine(limit=_DEFAULT_PURGE_BATCH_SIZE)
        self.purge_expired(limit=_DEFAULT_PURGE_BATCH_SIZE)
        with self._vault._mutation_lock:  # noqa: SLF001
            views = [self.view(item) for item in self._load_index()]
        views.sort(key=lambda view: view.item.deleted_at, reverse=True)
        return views

    def _entry(self, entry_id: str) -> tuple[list[TrashItem], TrashItem]:
        items = self._load_index()
        for item in items:
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
            self.purge_expired(limit=_DEFAULT_PURGE_BATCH_SIZE)
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
            relative_parent = vault.safety.display_path(parent)
            vault.safety.assert_safe_existing(parent, relative_path=relative_parent)
            try:
                os.rename(blob, target)
            except OSError as exc:
                raise AtomicWriteError("Trash entry could not be restored") from exc

            holder = blob.parent
            remaining = [entry for entry in items if entry.id != item.id]
            try:
                self._write_index(remaining)
            except Exception:
                # Keep the index authoritative when restoring cannot commit.
                with suppress(OSError):
                    os.rename(target, blob)
                raise
            with suppress(OSError):
                holder.rmdir()
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
        """Remove one entry permanently via rollback-safe quarantine."""
        with self._vault._mutation_lock:  # noqa: SLF001
            items, item = self._entry(entry_id)
            quarantine = self._quarantine_payload(item)
            try:
                self._write_index([entry for entry in items if entry.id != item.id])
            except Exception:
                self._restore_quarantine(item, quarantine)
                raise
        self._delete_quarantine(quarantine)
        return item.original_path

    def empty(self) -> int:
        """Permanently remove every entry; returns how many were removed."""
        with self._vault._mutation_lock:  # noqa: SLF001
            items = self._load_index()
            quarantined: list[tuple[TrashItem, Path | None]] = []
            try:
                for item in items:
                    quarantined.append((item, self._quarantine_payload(item)))
                if items:
                    self._write_index([])
            except Exception:
                for item, quarantine in reversed(quarantined):
                    self._restore_quarantine(item, quarantine)
                raise
        for _item, quarantine in quarantined:
            if quarantine is not None:
                self._delete_quarantine(quarantine)
        return len(items)

    def _quarantine_payload(self, item: TrashItem) -> Path | None:
        """Atomically detach a payload so index failure can restore it."""
        self._blob_path(item)
        holder = self.root / item.id
        try:
            info = holder.lstat()
        except FileNotFoundError:
            return None
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise InvalidOperation("Trash entry must be a directory")
        quarantine = self.root / f".delete-{item.id}-{uuid.uuid4().hex[:8]}"
        try:
            os.rename(holder, quarantine)
        except OSError as exc:
            raise AtomicWriteError("Trash entry could not be quarantined") from exc
        return quarantine

    def _restore_quarantine(self, item: TrashItem, quarantine: Path | None) -> None:
        if quarantine is None:
            return
        with suppress(OSError):
            os.rename(quarantine, self.root / item.id)

    @staticmethod
    def _delete_quarantine(quarantine: Path) -> None:
        try:
            shutil.rmtree(quarantine)
        except OSError:
            # Leave the detached tree for maintenance rather than restoring it
            # after its index record has been committed away.
            pass

    def purge_expired(self, *, now: datetime | None = None, limit: int | None = None) -> int:
        """Drop expired entries, optionally capped to a bounded batch.

        With ``limit`` this is safe for request paths: at most that many trash
        entries are traversed/deleted per call. Passing ``None`` drains all for
        explicit maintenance operations.
        """
        invalid_limit = limit is not None and (
            not isinstance(limit, int) or isinstance(limit, bool) or limit < 1
        )
        if invalid_limit:
            raise ValueError("limit must be a positive integer")
        moment = now or datetime.now(UTC)
        with self._vault._mutation_lock:  # noqa: SLF001
            items = self._load_index()
            if not items:
                return 0
            kept: list[TrashItem] = []
            expired: list[TrashItem] = []
            for item in items:
                if self._expires_at(item) <= moment and (limit is None or len(expired) < limit):
                    expired.append(item)
                else:
                    kept.append(item)
            if not expired:
                return 0
            moved: list[tuple[TrashItem, Path]] = []
            try:
                for item in expired:
                    holder = self.root / item.id
                    if not holder.exists() and not holder.is_symlink():
                        continue
                    self._blob_path(item)
                    quarantine = self.root / f"{_PURGE_DIR_PREFIX}{item.id}-{uuid.uuid4().hex[:8]}"
                    os.rename(holder, quarantine)
                    moved.append((item, quarantine))
                self._write_index(kept)
            except Exception:
                for item, quarantine in reversed(moved):
                    with suppress(OSError):
                        os.rename(quarantine, self.root / item.id)
                raise

        # Recursive deletion may be arbitrarily expensive; the tree is already
        # detached from the index and remains a recoverable quarantine orphan.
        for _item, quarantine in moved:
            try:
                shutil.rmtree(quarantine)
            except OSError:
                pass  # retry on a later list via _clean_quarantine
        return len(expired)

    def _clean_quarantine(self, *, limit: int) -> int:
        """Claim abandoned quarantine trees under lock, delete them outside."""
        claimed: list[Path] = []
        with self._vault._mutation_lock:  # noqa: SLF001
            try:
                self._ensure_root()
                entries = sorted(
                    [*self.root.glob(f"{_PURGE_DIR_PREFIX}*"), *self.root.glob(".delete-*")]
                )[:limit]
                indexed_ids = {item.id for item in self._load_index()}
            except (OSError, VaultUnavailable):
                return 0
            for entry in entries:
                if entry.name.startswith(".delete-"):
                    identifier = entry.name[len(".delete-") :].split("-", 1)[0]
                    if identifier in indexed_ids:
                        with suppress(OSError):
                            os.rename(entry, self.root / identifier)
                        continue
                claim = self.root / f".gc-{uuid.uuid4().hex}"
                try:
                    os.rename(entry, claim)
                    claimed.append(claim)
                except OSError:
                    continue
        removed = 0
        for entry in claimed:
            try:
                info = entry.lstat()
                if stat.S_ISLNK(info.st_mode):
                    entry.unlink()
                elif stat.S_ISDIR(info.st_mode):
                    shutil.rmtree(entry)
                else:
                    entry.unlink()
                removed += 1
            except OSError:
                continue  # retain for the next retry
        return removed

    def _remove_payload(self, item: TrashItem) -> None:
        """Delete a payload; a payload that is already gone is not an error."""
        root = self.root
        payload = self._blob_path(item)
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
            payload_info = payload.lstat()
            if stat.S_ISLNK(payload_info.st_mode):
                raise InvalidOperation("Trash payload must not be a symbolic link")
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
