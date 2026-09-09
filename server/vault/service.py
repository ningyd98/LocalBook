"""The single filesystem façade for LocalNote's local Vault (M1)."""

from __future__ import annotations

import hashlib
import mimetypes
import os
import stat
import threading
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from ..config import VaultSettings
from .atomic_write import (
    ReplayableStream,
    atomic_create_bytes,
    atomic_create_stream,
    atomic_replace_bytes,
    iter_chunks,
)
from .attachments import normalize_target_directory, safe_attachment_name
from .derived import initialize_derived
from .errors import (
    AlreadyExists,
    AtomicWriteError,
    ExpectedHashRequired,
    FileConflict,
    FileTooLarge,
    InvalidOperation,
    InvalidRequest,
    NotAFile,
    PathNotFound,
    SymlinkEscapeError,
    VaultNotConfigured,
    VaultUnavailable,
)
from .events import VaultEventCallback
from .path_safety import (
    PathSafety,
    is_hidden_name,
    is_reserved_derived_path,
    validate_relative_path,
)
from .schemas import VaultFileEntry
from .watcher import VaultWatcher

_CHUNK_SIZE = 1024 * 1024

_MARKDOWN_SUFFIXES = (".md", ".markdown")

# Number of collision retries before the atomic commit's ``AlreadyExists`` is
# surfaced as a 409.  Bounded so a hostile concurrent writer cannot spin.
_ATTACHMENT_NAME_RETRIES = 5


def sha256_bytes(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _content_type_for(relative_path: str) -> str:
    """Media type for display metadata (never used to decide readability)."""
    if relative_path.casefold().endswith(_MARKDOWN_SUFFIXES):
        return "text/markdown"
    return mimetypes.guess_type(relative_path)[0] or "application/octet-stream"


def _resource_content_type(relative_path: str) -> str:
    """Media type for the read-only raw resource endpoint.

    Unknown types fall back to ``application/octet-stream`` so a browser never
    executes an uploaded file as HTML.
    """
    guessed = mimetypes.guess_type(relative_path)[0]
    if guessed is None:
        return "application/octet-stream"
    if guessed in {"text/html", "application/xhtml+xml", "image/svg+xml"}:
        # Never let a raw Vault file be interpreted as active content.
        return "application/octet-stream"
    return guessed


def _content_disposition(relative_path: str) -> str:
    """Safe ``inline`` disposition with a sanitised display filename.

    HTTP header values are latin-1 on the wire, so the UTF-8 display name is
    only ever emitted through the RFC 5987 ``filename*`` parameter, fully
    percent-encoded; the bare ``filename`` fallback stays ASCII-only.
    """
    name = relative_path.split("/")[-1]
    # ASCII-only fallback: ``str.isalnum`` accepts CJK/emoji, which latin-1
    # header encoding would reject.
    ascii_name = "".join(
        char if (char.isascii() and (char.isalnum() or char in "._-")) else "_"
        for char in name
    ).strip("_") or "attachment"
    return (
        f'inline; filename="{ascii_name}"; '
        f"filename*=UTF-8''{_percent_encode(name)}"
    )


def _percent_encode(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")


def _digest_file(path: Path, *, max_bytes: int | None = None) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    try:
        with path.open("rb") as stream:
            while True:
                chunk = stream.read(_CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if max_bytes is not None and total > max_bytes:
                    raise FileTooLarge(path=None)
                digest.update(chunk)
    except FileTooLarge:
        raise
    except FileNotFoundError as exc:
        raise PathNotFound() from exc
    except OSError as exc:
        raise VaultUnavailable("Vault file cannot be read") from exc
    return f"sha256:{digest.hexdigest()}", total


def _read_file(path: Path, *, max_bytes: int) -> bytes:
    try:
        size = path.stat().st_size
    except FileNotFoundError as exc:
        raise PathNotFound() from exc
    except OSError as exc:
        raise VaultUnavailable("Vault file cannot be inspected") from exc
    if size > max_bytes:
        raise FileTooLarge()
    try:
        data = path.read_bytes()
    except FileNotFoundError as exc:
        raise PathNotFound() from exc
    except OSError as exc:
        raise VaultUnavailable("Vault file cannot be read") from exc
    if len(data) > max_bytes:
        raise FileTooLarge()
    return data


class VaultService:
    """Safe root-relative file operations.

    This class is intentionally the only module that performs Vault file I/O.
    Routes receive relative strings and call these methods; they never receive
    or manipulate filesystem ``Path`` objects from user input.
    """

    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        max_file_bytes: int = 50 * 1024 * 1024,
        watcher_enabled: bool = True,
        watcher_debounce_ms: int = 200,
        event_callback: VaultEventCallback | None = None,
        watcher_factory: Callable[..., VaultWatcher] | None = None,
        before_replace_hook: Callable[[Path], None] | None = None,
        initialize: bool = False,
    ) -> None:
        if max_file_bytes <= 0:
            raise ValueError("max_file_bytes must be positive")
        self.safety = PathSafety(root)
        self.max_file_bytes = max_file_bytes
        self._mutation_lock = threading.RLock()
        self._before_replace_hook = before_replace_hook
        self._initialized = False
        self._derived_path: Path | None = None
        self.watcher: VaultWatcher | None = None
        self._watcher_factory = watcher_factory
        self._watcher_enabled = watcher_enabled
        self._watcher_debounce_ms = watcher_debounce_ms
        self._event_callback = event_callback
        if initialize:
            self.initialize()

    @classmethod
    def from_settings(
        cls,
        settings: VaultSettings,
        *,
        event_callback: VaultEventCallback | None = None,
        watcher_factory: Callable[..., VaultWatcher] | None = None,
    ) -> VaultService:
        if settings.root is None:
            raise VaultNotConfigured()
        return cls(
            settings.root,
            max_file_bytes=settings.max_file_bytes,
            watcher_enabled=settings.watcher_enabled,
            watcher_debounce_ms=settings.watcher_debounce_ms,
            event_callback=event_callback,
            watcher_factory=watcher_factory,
        )

    @property
    def root(self) -> Path:
        """Internal diagnostic root; API responses never expose this value."""
        return self.safety.root

    @property
    def initialized(self) -> bool:
        return self._initialized

    @property
    def watcher_status(self) -> str:
        return self.watcher.status if self.watcher is not None else "disabled"

    @property
    def watcher_diagnostic(self) -> str | None:
        return self.watcher.diagnostic if self.watcher is not None else None

    def initialize(self, *, start_watcher: bool = True) -> None:
        """Validate root, initialize deletable derived state, optionally watch."""
        with self._mutation_lock:
            self.safety.assert_root_available()
            self._derived_path = initialize_derived(self.safety.root)
            self._initialized = True
            if self.watcher is None:
                self.watcher = self._make_watcher()
            if start_watcher and self.watcher is not None:
                self.watcher.start()

    def set_event_callback(self, callback: VaultEventCallback | None) -> None:
        """Attach or replace the watcher consumer.

        M3 additive helper (PLAN-M3 §5.4): it lets the app build the derived
        index first and only then route watcher events into it, while keeping
        every existing public signature unchanged.  The watcher exposes
        ``callback`` publicly, so a created-but-not-started watcher is updated
        in place before ``start()`` is called.
        """
        with self._mutation_lock:
            self._event_callback = callback
            if self.watcher is not None:
                self.watcher.callback = callback

    def _make_watcher(self) -> VaultWatcher:
        if self._watcher_factory is not None:
            try:
                return self._watcher_factory(
                    self.safety,
                    callback=self._event_callback,
                    debounce_ms=self._watcher_debounce_ms,
                    enabled=self._watcher_enabled,
                )
            except TypeError:
                # Friendly support for simple test factories accepting only the
                # safety object.
                return self._watcher_factory(self.safety)
        return VaultWatcher(
            self.safety,
            callback=self._event_callback,
            debounce_ms=self._watcher_debounce_ms,
            enabled=self._watcher_enabled,
        )

    def start(self) -> None:
        if not self._initialized:
            self.initialize()
        elif self.watcher is not None:
            self.watcher.start()

    def stop(self, timeout: float = 2.0) -> None:
        if self.watcher is not None:
            self.watcher.flush()
            self.watcher.stop(timeout=timeout)

    def flush(self) -> None:
        if self.watcher is not None:
            self.watcher.flush()

    def _validate_public_path(self, value: object, *, allow_root: bool = False) -> str:
        relative = validate_relative_path(value, allow_root=allow_root)
        if is_reserved_derived_path(relative):
            # .localnote is service-owned and cannot be read or mutated through
            # the public file API, even when include_hidden is requested.
            raise InvalidOperation("The .localnote directory is reserved")
        return relative

    def _resolve_existing_file(self, value: object) -> tuple[str, Path]:
        relative = self._validate_public_path(value)
        path = self.safety.resolve_existing_file(relative)
        self.safety.assert_safe_existing(path, relative_path=relative)
        return relative, path

    def _resolve_parent_and_target(self, value: object) -> tuple[str, Path, Path]:
        relative = self._validate_public_path(value)
        # resolve_parent validates existing, non-symlink parent and the target
        # containment.  It intentionally does not require the target to exist.
        _, parent, target = self.safety.resolve_parent(relative)
        self.safety.assert_safe_existing(parent, relative_path=self.safety.display_path(parent))
        return relative, parent, target

    def read_bytes(self, relative_path: str) -> tuple[bytes, str]:
        relative, path = self._resolve_existing_file(relative_path)
        with self._mutation_lock:
            self.safety.assert_safe_existing(path, relative_path=relative)
            data = _read_file(path, max_bytes=self.max_file_bytes)
            # Re-check after reading so a symlink replacement cannot be reported
            # as a successful read of an outside target.
            self.safety.assert_safe_existing(path, relative_path=relative)
        return data, sha256_bytes(data)

    def read_file(self, relative_path: str) -> dict[str, Any]:
        data, digest = self.read_bytes(relative_path)
        relative = self._validate_public_path(relative_path)
        return {
            "path": relative,
            "content": data,
            "content_base64": __import__("base64").b64encode(data).decode("ascii"),
            "byte_length": len(data),
            "sha256": digest,
            "content_type": _content_type_for(relative),
        }

    def _snapshot(self, relative: str, path: Path) -> tuple[bytes, str]:
        self.safety.assert_safe_existing(path, relative_path=relative)
        data = _read_file(path, max_bytes=self.max_file_bytes)
        self.safety.assert_safe_existing(path, relative_path=relative)
        return data, sha256_bytes(data)

    def create_bytes(self, relative_path: str, data: bytes) -> dict[str, Any]:
        if len(data) > self.max_file_bytes:
            raise FileTooLarge(path=relative_path)
        relative, parent, target = self._resolve_parent_and_target(relative_path)
        with self._mutation_lock:
            self.safety.assert_safe_existing(parent, relative_path=self.safety.display_path(parent))
            # Check target immediately before the atomic no-overwrite create.
            try:
                info = target.lstat()
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise VaultUnavailable("Vault target cannot be inspected", path=relative) from exc
            else:
                if stat.S_ISLNK(info.st_mode):
                    raise SymlinkEscapeError(path=relative)
                raise AlreadyExists(path=relative)
            try:
                atomic_create_bytes(target, data)
            except AlreadyExists as exc:
                exc.path = relative
                raise
            except (AtomicWriteError, OSError):
                raise
            self.safety.assert_safe_existing(target, relative_path=relative)
        return {
            "path": relative,
            "sha256": sha256_bytes(data),
            "byte_length": len(data),
            "operation": "created",
        }

    def write_bytes(
        self,
        relative_path: str,
        data: bytes,
        expected_sha256: str | None,
    ) -> dict[str, Any]:
        if len(data) > self.max_file_bytes:
            raise FileTooLarge(path=relative_path)
        relative, path = self._resolve_existing_file(relative_path)
        with self._mutation_lock:
            _, snapshot_a = self._snapshot(relative, path)
            expected = _require_expected_hash(relative, expected_sha256)
            if expected != snapshot_a:
                raise FileConflict(path=relative)

            def before_replace() -> None:
                self.safety.assert_safe_existing(path, relative_path=relative)
                _, snapshot_b = self._snapshot(relative, path)
                if snapshot_b != snapshot_a:
                    raise FileConflict(path=relative)
                if self._before_replace_hook is not None:
                    self._before_replace_hook(path)
                # Hook may intentionally simulate an external modification.
                self.safety.assert_safe_existing(path, relative_path=relative)
                _, snapshot_c = self._snapshot(relative, path)
                if snapshot_c != snapshot_a:
                    raise FileConflict(path=relative)

            atomic_replace_bytes(path, data, before_replace=before_replace)
            self.safety.assert_safe_existing(path, relative_path=relative)
            final_data = _read_file(path, max_bytes=self.max_file_bytes)
        return {
            "path": relative,
            "sha256": sha256_bytes(final_data),
            "byte_length": len(final_data),
            "operation": "updated",
        }

    def update_bytes(
        self,
        relative_path: str,
        data: bytes,
        expected_sha256: str | None,
    ) -> dict[str, Any]:
        return self.write_bytes(relative_path, data, expected_sha256)

    def create_file(self, relative_path: str, data: bytes) -> dict[str, Any]:
        return self.create_bytes(relative_path, data)

    def write_file(
        self,
        relative_path: str,
        data: bytes,
        expected_sha256: str | None,
    ) -> dict[str, Any]:
        return self.write_bytes(relative_path, data, expected_sha256)

    def create_directory(self, relative_path: str) -> dict[str, Any]:
        """Create a directory. Parents must exist; never overwrites a file."""
        relative, parent, target = self._resolve_parent_and_target(relative_path)
        with self._mutation_lock:
            self.safety.assert_safe_existing(parent, relative_path=self.safety.display_path(parent))
            try:
                info = target.lstat()
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise VaultUnavailable(
                    "Vault target cannot be inspected", path=relative
                ) from exc
            else:
                if stat.S_ISLNK(info.st_mode):
                    raise SymlinkEscapeError(path=relative)
                # A directory that already exists is a conflict, exactly like a file.
                raise AlreadyExists(path=relative)
            # Revalidate immediately before the syscall.
            self.safety.assert_safe_existing(parent, relative_path=self.safety.display_path(parent))
            try:
                target.mkdir(mode=0o755)
            except FileExistsError as exc:
                raise AlreadyExists(path=relative) from exc
            except OSError as exc:
                raise AtomicWriteError("Vault directory could not be created") from exc
            self.safety.assert_safe_existing(target, relative_path=relative)
        return {"path": relative, "sha256": None, "byte_length": None, "operation": "created"}

    # ------------------------------------------------------------------
    # Attachment uploads and raw resource reads (PLAN-ATTACHMENTS v1.1).
    #
    # The front end decides ``target_directory`` per entry point; the service
    # only validates and executes it.  An empty string means the Vault root.
    # No intermediate directory is ever created: the target directory must
    # already exist and must not be a symlink.
    # ------------------------------------------------------------------

    def _resolve_upload_directory(self, target_directory: object) -> tuple[str, Path]:
        directory = normalize_target_directory(target_directory)
        path = self.safety.resolve_existing_directory(
            directory or ".", allow_root=True
        )
        self.safety.assert_safe_existing(
            path, relative_path=directory or self.safety.display_path(path)
        )
        return directory, path

    def _existing_child_names(self, directory: Path) -> set[str]:
        try:
            return {entry.name for entry in os.scandir(directory)}
        except FileNotFoundError as exc:
            raise PathNotFound() from exc
        except OSError as exc:
            raise VaultUnavailable("Vault directory cannot be listed") from exc

    def _attachment_response(
        self,
        relative: str,
        digest: str,
        byte_length: int,
        original_name: str,
    ) -> dict[str, Any]:
        return {
            "path": relative,
            "sha256": digest,
            "byte_length": byte_length,
            "content_type": _content_type_for(relative),
            "operation": "created",
            "original_name": original_name,
        }

    def _upload_attachment(
        self,
        original_name: object,
        target_directory: object,
        *,
        content: bytes | None = None,
        stream: Iterable[bytes] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(original_name, str) or not original_name.strip():
            raise InvalidRequest("original_name must be a non-empty string")
        display_name = original_name.strip()
        if content is not None and len(content) > self.max_file_bytes:
            raise FileTooLarge()
        directory, directory_path = self._resolve_upload_directory(target_directory)

        with self._mutation_lock:
            self.safety.assert_safe_existing(
                directory_path, relative_path=directory or "."
            )
            existing = self._existing_child_names(directory_path)
            # A symlink anywhere in the chosen name's path is a security error
            # and must be rejected before it is treated as a name collision:
            # inspecting every candidate (not only the free ones) keeps an
            # exposed symlink from being silently skipped as "already taken".
            for name in sorted(existing):
                candidate = self.safety.root_real.joinpath(
                    *(([directory] if directory else []) + [name])
                )
                try:
                    if stat.S_ISLNK(candidate.lstat().st_mode):
                        raise SymlinkEscapeError(
                            path=f"{directory}/{name}" if directory else name
                        )
                except FileNotFoundError:
                    continue
                except OSError as exc:
                    raise VaultUnavailable(
                        "Vault entry cannot be inspected"
                    ) from exc
            taken = set(existing)
            last_error: AlreadyExists | None = None
            for _attempt in range(_ATTACHMENT_NAME_RETRIES):
                relative = safe_attachment_name(
                    display_name,
                    directory,
                    taken,
                    None,
                )
                target = self.safety.root_real.joinpath(*relative.split("/"))
                # Re-check the directory immediately before the syscall; the
                # target itself is inspected again for a late symlink swap.
                self.safety.assert_safe_existing(
                    directory_path, relative_path=directory or "."
                )
                try:
                    info = target.lstat()
                except FileNotFoundError:
                    pass
                else:
                    if stat.S_ISLNK(info.st_mode):
                        raise SymlinkEscapeError(path=relative)
                    taken.add(target.name)
                    last_error = AlreadyExists(path=relative)
                    continue
                try:
                    if stream is None:
                        atomic_create_bytes(target, content or b"")
                        digest = sha256_bytes(content or b"")
                        byte_length = len(content or b"")
                    else:
                        digest, byte_length = atomic_create_stream(
                            target,
                            stream,
                            max_bytes=self.max_file_bytes,
                        )
                    self.safety.assert_safe_existing(
                        directory_path, relative_path=directory or "."
                    )
                    self.safety.assert_safe_existing(target, relative_path=relative)
                except AlreadyExists as exc:
                    exc.path = relative
                    taken.add(target.name)
                    last_error = exc
                    continue
                return self._attachment_response(
                    relative, digest, byte_length, display_name
                )
        raise last_error or AlreadyExists(
            path=safe_attachment_name(display_name, directory, taken)
        )

    def upload_attachment_bytes(
        self,
        original_name: str,
        target_directory: str,
        data: bytes,
    ) -> dict[str, Any]:
        """JSON-channel upload of a small attachment (≤ the caller's threshold)."""
        return self._upload_attachment(
            original_name,
            target_directory,
            content=data,
        )

    def upload_attachment_stream(
        self,
        original_name: str,
        target_directory: str,
        stream: object,
        *,
        chunk_size: int = _CHUNK_SIZE,
    ) -> dict[str, Any]:
        """Multipart-channel upload; the payload is never aggregated in memory.

        The bounded reader is wrapped so a name-collision retry can replay the
        already-read chunks instead of losing them.
        """
        if hasattr(stream, "read") or isinstance(stream, (bytes, bytearray, memoryview)):
            replayable: Iterable[bytes] = ReplayableStream(
                stream,
                max_bytes=self.max_file_bytes,
                chunk_size=chunk_size,
            )
        else:
            replayable = iter_chunks(stream, chunk_size=chunk_size)
        return self._upload_attachment(
            original_name,
            target_directory,
            stream=replayable,
        )

    def open_resource(self, relative_path: str) -> dict[str, Any]:
        """Resolve a read-only raw resource for the preview/download endpoint.

        Returns a bounded reader; the caller streams it and closes it.  The
        path uses the same public validation as every other Vault read, so
        symlinks, hidden segments and ``.localnote`` are rejected.
        """
        relative, path = self._resolve_existing_file(relative_path)
        with self._mutation_lock:
            info = self.safety.assert_safe_existing(path, relative_path=relative)
            if not stat.S_ISREG(info.st_mode):
                raise NotAFile(path=relative)
            if info.st_size > self.max_file_bytes:
                raise FileTooLarge(path=relative)
            try:
                handle = path.open("rb")
            except FileNotFoundError as exc:
                raise PathNotFound(path=relative) from exc
            except OSError as exc:
                raise VaultUnavailable("Vault file cannot be read", path=relative) from exc
        return {
            "path": relative,
            "byte_length": info.st_size,
            "content_type": _resource_content_type(relative),
            "content_disposition": _content_disposition(relative),
            "reader": handle,
            "max_bytes": self.max_file_bytes,
            "owner": self,
        }

    def close_resource(self, resource: dict[str, Any]) -> None:
        reader = resource.get("reader")
        if reader is not None:
            try:
                reader.close()
            except OSError:
                pass

    def delete_file(self, relative_path: str, expected_sha256: str | None) -> dict[str, Any]:
        relative, path = self._resolve_existing_file(relative_path)
        with self._mutation_lock:
            _, actual = self._snapshot(relative, path)
            if _require_expected_hash(relative, expected_sha256) != actual:
                raise FileConflict(path=relative)
            self.safety.assert_safe_existing(path, relative_path=relative)
            # Second hash pass immediately before the unlink narrows the
            # snapshot→syscall window for an external rewrite.
            _, actual_again = self._snapshot(relative, path)
            if actual_again != actual:
                raise FileConflict(path=relative)
            try:
                path.unlink()
            except FileNotFoundError as exc:
                raise FileConflict(path=relative) from exc
            except OSError as exc:
                raise AtomicWriteError("Vault file could not be deleted") from exc
        return {"path": relative, "sha256": None, "byte_length": None, "operation": "deleted"}

    def delete(self, relative_path: str, expected_sha256: str | None) -> dict[str, Any]:
        return self.delete_file(relative_path, expected_sha256)

    def move_file(
        self,
        source_path: str,
        destination_path: str,
        expected_sha256: str | None,
    ) -> dict[str, Any]:
        source_relative, source = self._resolve_existing_file(source_path)
        destination_relative, parent, destination = self._resolve_parent_and_target(
            destination_path
        )
        if source_relative == destination_relative:
            raise InvalidOperation("Source and destination must differ", path=source_relative)
        with self._mutation_lock:
            _, actual = self._snapshot(source_relative, source)
            if _require_expected_hash(source_relative, expected_sha256) != actual:
                raise FileConflict(path=source_relative)
            self.safety.assert_safe_existing(parent, relative_path=self.safety.display_path(parent))
            try:
                info = destination.lstat()
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise VaultUnavailable(
                    "Vault destination cannot be inspected", path=destination_relative
                ) from exc
            else:
                if stat.S_ISLNK(info.st_mode):
                    raise SymlinkEscapeError(path=destination_relative)
                raise AlreadyExists(path=destination_relative)
            # Revalidate both endpoints immediately before the syscall.
            self.safety.assert_safe_existing(source, relative_path=source_relative)
            self.safety.assert_safe_existing(parent, relative_path=self.safety.display_path(parent))
            try:
                # ``os.rename`` is atomic and never crosses devices, but on
                # POSIX it replaces an existing target.  The exclusive hard
                # link + unlink sequence below supplies no-overwrite semantics
                # while staying on the same filesystem; failure never falls
                # back to copy-delete.
                os.link(source, destination, follow_symlinks=False)
            except FileExistsError as exc:
                raise AlreadyExists(path=destination_relative) from exc
            except OSError as exc:
                if exc.errno == getattr(os, "EXDEV", 18):
                    raise AtomicWriteError("Vault move cannot cross devices") from exc
                raise AtomicWriteError("Vault move failed") from exc
            try:
                self.safety.assert_safe_existing(destination, relative_path=destination_relative)
                _, destination_hash = self._snapshot(destination_relative, destination)
                if destination_hash != actual:
                    raise FileConflict(path=source_relative)
                # A final source check closes the most relevant in-process race
                # before unlinking the old name.
                _, source_hash = self._snapshot(source_relative, source)
                if source_hash != actual:
                    raise FileConflict(path=source_relative)
                source.unlink()
            except Exception:
                try:
                    destination.unlink(missing_ok=True)
                except OSError:
                    pass
                raise
        return {
            "path": destination_relative,
            "sha256": actual,
            "byte_length": len(_read_file(destination, max_bytes=self.max_file_bytes)),
            "operation": "moved",
        }

    def move(
        self,
        source_path: str,
        destination_path: str,
        expected_sha256: str | None,
    ) -> dict[str, Any]:
        return self.move_file(source_path, destination_path, expected_sha256)

    def list_tree(
        self,
        relative_path: str | None = None,
        *,
        recursive: bool = True,
        include_hidden: bool = False,
    ) -> list[VaultFileEntry]:
        requested = (
            "."
            if relative_path in (None, "", ".")
            else self._validate_public_path(relative_path)
        )
        directory = self.safety.resolve_existing_directory(requested, allow_root=True)
        entries: list[VaultFileEntry] = []

        def visit(current: Path) -> None:
            self.safety.assert_safe_existing(
                current, relative_path=self.safety.display_path(current)
            )
            try:
                children = list(os.scandir(current))
            except OSError as exc:
                raise VaultUnavailable("Vault directory cannot be listed") from exc
            for entry in children:
                name = entry.name
                child = current / name
                display = self.safety.display_path(child)
                if is_reserved_derived_path(display):
                    continue
                if not include_hidden and is_hidden_name(name):
                    continue
                try:
                    info = child.lstat()
                except FileNotFoundError:
                    continue
                except OSError as exc:
                    raise VaultUnavailable("Vault entry cannot be inspected") from exc
                if stat.S_ISLNK(info.st_mode):
                    # Conservative policy: an exposed symlink is a security
                    # error, never an outside target disguised as a file.
                    raise SymlinkEscapeError(path=display)
                if stat.S_ISDIR(info.st_mode):
                    entries.append(VaultFileEntry(path=display, kind="directory"))
                    if recursive:
                        visit(child)
                elif stat.S_ISREG(info.st_mode):
                    size = info.st_size
                    digest: str | None = None
                    if size <= self.max_file_bytes:
                        try:
                            digest, _ = _digest_file(child, max_bytes=self.max_file_bytes)
                        except FileTooLarge:
                            # File grew between lstat and hashing; still list it
                            # without a digest rather than failing the whole tree.
                            digest = None
                    entries.append(
                        VaultFileEntry(path=display, kind="file", size=size, sha256=digest)
                    )
                else:
                    # Devices/sockets/FIFOs are not user files and are not
                    # exposed through the API.
                    continue

        with self._mutation_lock:
            visit(directory)
        entries.sort(key=lambda item: item.path)
        return entries

    def list_files(
        self,
        relative_path: str | None = None,
        *,
        recursive: bool = True,
        include_hidden: bool = False,
    ) -> list[VaultFileEntry]:
        return self.list_tree(relative_path, recursive=recursive, include_hidden=include_hidden)


def _canonical_digest(value: str) -> str:
    text = str(value).lower()
    if text.startswith("sha256:"):
        return text
    return f"sha256:{text}"


def _require_expected_hash(relative_path: str, expected_sha256: str | None) -> str:
    """Return the digest or raise the §4.4 400 domain error."""
    if not expected_sha256:
        raise ExpectedHashRequired(path=relative_path)
    return _canonical_digest(expected_sha256)


__all__ = ["VaultService", "sha256_bytes"]
