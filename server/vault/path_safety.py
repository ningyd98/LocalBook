"""Root-relative path validation and filesystem safety checks.

The resolver uses a conservative policy: no user-reachable symbolic link is
accepted, even when it points back inside the configured root.  Lexical
validation always precedes resolution, and every operation can re-run the
checks immediately before its filesystem syscall.
"""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

from .errors import (
    InvalidRequest,
    NotADirectory,
    NotAFile,
    PathNotFound,
    PathTraversalError,
    SymlinkEscapeError,
    VaultUnavailable,
)
from .schemas import normalize_relative_path

_MAX_PATH_BYTES = 4096
_DRIVE_PREFIX_RE = re.compile(r"^[A-Za-z]:")


def validate_relative_path(value: object, *, allow_root: bool = False) -> str:
    """Validate one API path using POSIX-relative lexical semantics.

    This function never touches the filesystem.  It rejects ``..``, absolute
    paths, drive/UNC/device prefixes, NULs, backslashes, empty components and
    dot components.  A caller may explicitly opt into ``.``/``""`` only for
    internal root-directory listing.
    """
    if allow_root and (value == "" or value == "."):
        return "."
    if not isinstance(value, str):
        raise InvalidRequest("Path must be a string")
    if not value:
        raise InvalidRequest("Path must not be empty")
    if "\x00" in value:
        raise InvalidRequest("Path must not contain NUL")
    # Treat all backslash forms as a traversal-class input.  Accepting them as
    # ordinary POSIX filename characters would make Windows/WSL semantics
    # surprising and could hide an absolute/UNC path from another platform.
    if "\\" in value:
        raise PathTraversalError("Path must use POSIX separators")
    if value.startswith("/") or value.startswith("//"):
        raise PathTraversalError("Path must be root-relative")
    if _DRIVE_PREFIX_RE.match(value):
        raise PathTraversalError("Path must not contain a drive prefix")
    if value.startswith("?"):
        raise PathTraversalError("Path must not contain a device prefix")
    try:
        return normalize_relative_path(value)
    except ValueError as exc:
        message = str(exc)
        if "unsafe segment" in message or "root-relative" in message:
            raise PathTraversalError("Path contains an unsafe segment") from exc
        if "drive prefix" in message or "UNC" in message:
            raise PathTraversalError("Path must be root-relative") from exc
        raise InvalidRequest("Path is not a valid root-relative POSIX path") from exc


def _relative_parts(relative_path: str) -> tuple[str, ...]:
    if relative_path == ".":
        return ()
    return tuple(relative_path.split("/"))


def _relative_display(root: Path, path: Path) -> str:
    relative = path.relative_to(root)
    return "." if not relative.parts else "/".join(relative.parts)


def _is_symlink_mode(mode: int) -> bool:
    return stat.S_ISLNK(mode)


def _safe_lstat(path: Path, *, relative_path: str | None = None) -> os.stat_result:
    try:
        return path.lstat()
    except FileNotFoundError as exc:
        raise PathNotFound(path=relative_path) from exc
    except OSError as exc:
        raise VaultUnavailable("Vault path cannot be inspected", path=relative_path) from exc


def _check_containment(root_real: Path, candidate_real: Path, *, path: str | None) -> None:
    try:
        candidate_real.relative_to(root_real)
    except ValueError as exc:
        raise PathTraversalError("Path resolves outside the configured Vault", path=path) from exc


def _check_existing_chain(
    root_real: Path,
    candidate: Path,
    *,
    relative_path: str | None,
    allow_missing_leaf: bool,
) -> None:
    """lstat each existing component without following user symlinks."""
    try:
        relative = candidate.relative_to(root_real)
    except ValueError as exc:
        raise PathTraversalError(
            "Path is outside the configured Vault", path=relative_path
        ) from exc

    current = root_real
    parts = relative.parts
    for part in parts:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError as exc:
            if allow_missing_leaf:
                # Missing intermediate components are left for the operation's
                # parent validation.  No currently reachable later component
                # can be inspected once this one is absent.
                return
            raise PathNotFound(path=relative_path) from exc
        except NotADirectoryError as exc:
            # A regular file (or other non-directory) sits in the middle of
            # the path: the caller must not treat this as a missing leaf.
            raise NotADirectory(path=relative_path) from exc
        except OSError as exc:
            raise VaultUnavailable("Vault path cannot be inspected", path=relative_path) from exc
        if _is_symlink_mode(info.st_mode):
            raise SymlinkEscapeError(path=relative_path)


def ensure_no_symlink(path: Path, *, relative_path: str | None = None) -> os.stat_result:
    """Reject a symlink at ``path`` and return its lstat result."""
    info = _safe_lstat(path, relative_path=relative_path)
    if _is_symlink_mode(info.st_mode):
        raise SymlinkEscapeError(path=relative_path)
    return info


class PathSafety:
    """Resolve and re-check paths below one immutable real Vault root."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        try:
            configured = Path(root).expanduser()
        except (TypeError, ValueError) as exc:
            raise VaultUnavailable("Vault root is invalid") from exc
        try:
            root_real = configured.resolve(strict=True)
            root_info = root_real.lstat()
        except FileNotFoundError as exc:
            raise VaultUnavailable("Vault root is unavailable") from exc
        except OSError as exc:
            raise VaultUnavailable("Vault root is unavailable") from exc
        if not stat.S_ISDIR(root_info.st_mode):
            raise VaultUnavailable("Vault root must be an existing directory")
        if len(os.fsencode(str(root_real))) > _MAX_PATH_BYTES:
            raise VaultUnavailable("Vault root is too long")
        self.root_real = root_real

    @property
    def root(self) -> Path:
        return self.root_real

    def assert_root_available(self) -> None:
        try:
            info = self.root_real.lstat()
        except (FileNotFoundError, OSError) as exc:
            raise VaultUnavailable("Vault root is unavailable") from exc
        if _is_symlink_mode(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise VaultUnavailable("Vault root is unavailable")

    def resolve(
        self,
        value: object,
        *,
        allow_root: bool = False,
        allow_missing: bool = True,
        require_directory: bool = False,
        require_file: bool = False,
    ) -> Path:
        """Validate, resolve and containment-check one root-relative path."""
        self.assert_root_available()
        relative_path = validate_relative_path(value, allow_root=allow_root)
        candidate = self.root_real.joinpath(*_relative_parts(relative_path))

        # Lexical component lstat comes before resolve so exposed symlinks are
        # consistently classified as symlink_escape.
        _check_existing_chain(
            self.root_real,
            candidate,
            relative_path=relative_path,
            allow_missing_leaf=allow_missing,
        )
        try:
            info = candidate.lstat()
        except FileNotFoundError as exc:
            if not allow_missing:
                raise PathNotFound(path=relative_path) from exc
            info = None
        except NotADirectoryError as exc:
            raise NotADirectory(path=relative_path) from exc
        except OSError as exc:
            raise VaultUnavailable("Vault path cannot be inspected", path=relative_path) from exc
        if info is not None:
            if _is_symlink_mode(info.st_mode):
                raise SymlinkEscapeError(path=relative_path)
            if require_directory and not stat.S_ISDIR(info.st_mode):
                raise NotADirectory(path=relative_path)
            if require_file and not stat.S_ISREG(info.st_mode):
                raise NotAFile(path=relative_path)

        try:
            candidate_real = candidate.resolve(strict=False)
        except OSError as exc:
            raise VaultUnavailable("Vault path cannot be resolved", path=relative_path) from exc
        _check_containment(self.root_real, candidate_real, path=relative_path)

        # Immediate pre-return recheck narrows the lstat/operation race window.
        _check_existing_chain(
            self.root_real,
            candidate,
            relative_path=relative_path,
            allow_missing_leaf=allow_missing,
        )
        try:
            candidate_real_again = candidate.resolve(strict=False)
        except OSError as exc:
            raise VaultUnavailable("Vault path cannot be resolved", path=relative_path) from exc
        _check_containment(self.root_real, candidate_real_again, path=relative_path)
        return candidate

    def resolve_existing_file(self, value: object) -> Path:
        return self.resolve(value, allow_missing=False, require_file=True)

    def resolve_existing_directory(self, value: object, *, allow_root: bool = False) -> Path:
        return self.resolve(
            value,
            allow_root=allow_root,
            allow_missing=False,
            require_directory=True,
        )

    def resolve_parent(self, value: object) -> tuple[str, Path, Path]:
        """Return ``(relative, parent, target)`` for a validated leaf."""
        relative = validate_relative_path(value)
        parts = _relative_parts(relative)
        parent = self.root_real.joinpath(*parts[:-1]) if len(parts) > 1 else self.root_real
        target = self.root_real.joinpath(*parts)
        parent_relative = "/".join(parts[:-1]) if len(parts) > 1 else "."
        self.resolve(
            parent_relative,
            allow_root=True,
            allow_missing=False,
            require_directory=True,
        )
        self.resolve(relative, allow_missing=True)
        return relative, parent, target

    def display_path(self, path: Path) -> str:
        return _relative_display(self.root_real, path)

    def assert_safe_existing(
        self,
        path: Path,
        *,
        relative_path: str | None = None,
    ) -> os.stat_result:
        """Re-run realpath/containment and lstat checks for a syscall."""
        self.assert_root_available()
        _check_existing_chain(
            self.root_real,
            path,
            relative_path=relative_path,
            allow_missing_leaf=False,
        )
        try:
            real = path.resolve(strict=False)
        except OSError as exc:
            raise VaultUnavailable("Vault path cannot be resolved", path=relative_path) from exc
        _check_containment(self.root_real, real, path=relative_path)
        return ensure_no_symlink(path, relative_path=relative_path)


def resolve_relative(
    root: str | os.PathLike[str],
    value: object,
    *,
    allow_root: bool = False,
    allow_missing: bool = True,
) -> Path:
    """Functional convenience wrapper used by tests and small integrations."""
    return PathSafety(root).resolve(
        value,
        allow_root=allow_root,
        allow_missing=allow_missing,
    )


def is_hidden_name(name: str) -> bool:
    return name.startswith(".")


def is_reserved_derived_path(relative_path: str) -> bool:
    return relative_path == ".localnote" or relative_path.startswith(".localnote/")


__all__ = [
    "PathSafety",
    "ensure_no_symlink",
    "is_hidden_name",
    "is_reserved_derived_path",
    "resolve_relative",
    "validate_relative_path",
]
