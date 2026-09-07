"""Domain errors for the M1 Vault core.

The domain layer deliberately has no FastAPI dependency.  Each public error
carries a stable machine-readable code and, when useful, a root-relative path.
Messages are fixed/sanitised so an API handler can safely expose them without
leaking the configured root, exception details, or file contents.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class VaultErrorCode(StrEnum):
    VAULT_NOT_CONFIGURED = "vault_not_configured"
    VAULT_UNAVAILABLE = "vault_unavailable"
    PATH_TRAVERSAL = "path_traversal"
    SYMLINK_ESCAPE = "symlink_escape"
    NOT_FOUND = "not_found"
    ALREADY_EXISTS = "already_exists"
    FILE_CONFLICT = "file_conflict"
    EXPECTED_HASH_REQUIRED = "expected_hash_required"
    INVALID_REQUEST = "invalid_request"
    # Canonical wire code from PLAN-M1.md §4.6 error enum (``file_too_large``),
    # mapped to HTTP 413.  (The plan text sometimes abbreviates this as
    # "file too large"/"too_large" when describing the HTTP mapping.)
    FILE_TOO_LARGE = "file_too_large"
    NOT_A_FILE = "not_a_file"
    NOT_A_DIRECTORY = "not_a_directory"
    ATOMIC_WRITE_FAILED = "atomic_write_failed"
    WATCHER_UNAVAILABLE = "watcher_unavailable"
    # M3 derived index is unavailable (PLAN-M3 §6.3): mapped to HTTP 503 and
    # only affects metadata/links/search endpoints, never Vault reads/writes.
    INDEX_UNAVAILABLE = "index_unavailable"
    INTERNAL_ERROR = "internal_error"


class VaultError(Exception):
    """Base class for expected, safe-to-serialise domain failures."""

    code: VaultErrorCode = VaultErrorCode.INTERNAL_ERROR
    default_message = "Vault operation failed"

    def __init__(self, message: str | None = None, *, path: str | None = None) -> None:
        self.message = message or self.default_message
        self.path = path
        super().__init__(self.message)

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "code": self.code.value,
            "message": self.message,
            "path": self.path,
        }
        return result


class VaultNotConfigured(VaultError):
    code = VaultErrorCode.VAULT_NOT_CONFIGURED
    default_message = "Vault is not configured"


class VaultUnavailable(VaultError):
    code = VaultErrorCode.VAULT_UNAVAILABLE
    default_message = "Vault is unavailable"


class PathTraversalError(VaultError):
    code = VaultErrorCode.PATH_TRAVERSAL
    default_message = "Path must be a safe root-relative path"


class SymlinkEscapeError(VaultError):
    code = VaultErrorCode.SYMLINK_ESCAPE
    default_message = "Symbolic links are not allowed in Vault paths"


class PathNotFound(VaultError):
    code = VaultErrorCode.NOT_FOUND
    default_message = "File or directory was not found"


class AlreadyExists(VaultError):
    code = VaultErrorCode.ALREADY_EXISTS
    default_message = "Destination already exists"


class FileConflict(VaultError):
    code = VaultErrorCode.FILE_CONFLICT
    default_message = "File changed externally; reload before writing"


class ExpectedHashRequired(VaultError):
    code = VaultErrorCode.EXPECTED_HASH_REQUIRED
    default_message = "expected_sha256 is required for updates"


class InvalidOperation(VaultError):
    code = VaultErrorCode.INVALID_REQUEST
    default_message = "Invalid Vault operation"


class FileTooLarge(VaultError):
    code = VaultErrorCode.FILE_TOO_LARGE
    default_message = "File exceeds the configured size limit"


class NotAFile(VaultError):
    code = VaultErrorCode.NOT_A_FILE
    default_message = "Path is not a regular file"


class NotADirectory(VaultError):
    code = VaultErrorCode.NOT_A_DIRECTORY
    default_message = "Path is not a directory"


class AtomicWriteError(VaultError):
    code = VaultErrorCode.ATOMIC_WRITE_FAILED
    default_message = "Atomic Vault write failed"


class WatcherError(VaultError):
    code = VaultErrorCode.WATCHER_UNAVAILABLE
    default_message = "Vault watcher is unavailable"


class InvalidRequest(VaultError):
    code = VaultErrorCode.INVALID_REQUEST
    default_message = "Invalid Vault request"


__all__ = [
    "AlreadyExists",
    "AtomicWriteError",
    "ExpectedHashRequired",
    "FileConflict",
    "FileTooLarge",
    "InvalidOperation",
    "InvalidRequest",
    "NotADirectory",
    "NotAFile",
    "PathNotFound",
    "PathTraversalError",
    "SymlinkEscapeError",
    "VaultError",
    "VaultErrorCode",
    "VaultNotConfigured",
    "VaultUnavailable",
    "WatcherError",
]
