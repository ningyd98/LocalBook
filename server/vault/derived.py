"""Minimal, deletable ``.localnote`` derived-state bootstrap (M1 → M4).

M1 creates no database, schema, index, cache, or content copy: the only
persistent artifact is a small state placeholder that can be deleted and
recreated without affecting Vault files.  M4 keeps this invariant for
*initialization* (``initialize_derived`` still leaves only ``state.json``):
the SQLite derived library is created later by the index layer as
``.localnote/<db_filename>`` (default ``index.db``).  That database remains
derived data — deleting the whole ``.localnote`` directory and rebuilding
reproduces it from the Markdown files, never the other way round.
"""

from __future__ import annotations

import json
import os
import secrets
import stat
from pathlib import Path

from .errors import AtomicWriteError, SymlinkEscapeError, VaultUnavailable

DERIVED_DIR_NAME = ".localnote"
STATE_FILE_NAME = "state.json"
STATE_PAYLOAD = {"format": "localnote-m1", "initialized": True, "version": 1}


def _ensure_directory(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            # Another process won the race; validate what is already there.
            pass
        try:
            info = path.lstat()
        except OSError as exc:
            raise VaultUnavailable("Vault derived state cannot be initialized") from exc
    except OSError as exc:
        raise VaultUnavailable("Vault derived state cannot be inspected") from exc
    if stat.S_ISLNK(info.st_mode):
        raise SymlinkEscapeError("Derived state path must not be a symbolic link")
    if not stat.S_ISDIR(info.st_mode):
        raise VaultUnavailable("Vault derived state path is not a directory")


def initialize_derived(root: Path) -> Path:
    """Create/reuse the M1 derived directory and state placeholder."""
    try:
        root_info = root.lstat()
    except OSError as exc:
        raise VaultUnavailable("Vault root is unavailable") from exc
    if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
        raise VaultUnavailable("Vault root is unavailable")

    derived = root / DERIVED_DIR_NAME
    _ensure_directory(derived)
    state = derived / STATE_FILE_NAME
    try:
        state_info = state.lstat()
    except FileNotFoundError:
        payload = (json.dumps(STATE_PAYLOAD, sort_keys=True, separators=(",", ":")) + "\n").encode()
        temp = derived / f".{STATE_FILE_NAME}.tmp-{os.getpid()}-{secrets.token_hex(8)}"
        try:
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(fd, "wb") as stream:
                    fd = -1
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp, state)
            finally:
                if fd >= 0:
                    os.close(fd)
                try:
                    temp.unlink(missing_ok=True)
                except OSError:
                    pass
        except FileExistsError:
            # A concurrent initializer created the state file; nothing to do.
            pass
        except OSError as exc:
            raise AtomicWriteError("Vault derived state could not be initialized") from exc
    except OSError as exc:
        raise VaultUnavailable("Vault derived state cannot be inspected") from exc
    else:
        if stat.S_ISLNK(state_info.st_mode):
            raise SymlinkEscapeError("Derived state must not be a symbolic link")
        if not stat.S_ISREG(state_info.st_mode):
            raise VaultUnavailable("Vault derived state is not a regular file")
    return derived


def derived_path(root: Path) -> Path:
    return root / DERIVED_DIR_NAME


def derived_db_path(root: Path, filename: str = "index.db") -> Path:
    """Path of the SQLite derived database inside ``.localnote`` (M4).

    The file is owned by the index layer: calling this does not create the
    directory or the database.  ``initialize_derived`` must have run first so
    that ``.localnote`` exists.
    """
    return root / DERIVED_DIR_NAME / filename


__all__ = [
    "DERIVED_DIR_NAME",
    "STATE_FILE_NAME",
    "STATE_PAYLOAD",
    "derived_db_path",
    "derived_path",
    "initialize_derived",
]
