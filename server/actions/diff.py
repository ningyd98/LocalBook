"""Raw-byte based, immutable diff metadata for M7 (PLAN-M7 §5.3).

Diffs are computed from the exact before/after bytes (never from Markdown
re-serialization), so BOMs, CRLF line endings and non-UTF-8 content keep their
original hashes.  A unified diff is only produced when both sides decode as
UTF-8 and fit the byte cap; otherwise the entry still carries the trusted
hashes and sizes.
"""

from __future__ import annotations

import difflib
import hashlib
from dataclasses import dataclass

_MAX_UNIFIED_DIFF_BYTES = 200_000


def sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class DiffEntry:
    """Immutable snapshot of one proposed/executed file change."""

    path: str
    operation: str  # create | update | delete
    before_hash: str | None
    after_hash: str | None
    before_size: int
    after_size: int
    unified_diff: str | None = None
    status: str = "proposed"


def build_diff(
    path: str,
    before: bytes | None,
    after: bytes | None,
    *,
    max_diff_bytes: int = _MAX_UNIFIED_DIFF_BYTES,
) -> DiffEntry:
    """Build one DiffEntry without touching the filesystem or any service."""
    old = before or b""
    new = after or b""
    operation = (
        "create"
        if before is None
        else "delete"
        if after is None
        else "update"
    )
    unified: str | None = None
    if before is not None and after is not None:
        try:
            old_lines = old.decode("utf-8").splitlines(keepends=True)
            new_lines = new.decode("utf-8").splitlines(keepends=True)
            unified = "".join(
                difflib.unified_diff(
                    old_lines,
                    new_lines,
                    fromfile=path,
                    tofile=path,
                )
            )
            if len(unified.encode("utf-8")) > max_diff_bytes:
                unified = None
        except UnicodeDecodeError:
            # Non-UTF-8 mutation: hash/size only (and such writes are denied by
            # policy before execution; this fallback exists for diff metadata).
            unified = None
    return DiffEntry(
        path=path,
        operation=operation,
        before_hash=sha256(old) if before is not None else None,
        after_hash=sha256(new) if after is not None else None,
        before_size=len(old) if before is not None else 0,
        after_size=len(new) if after is not None else 0,
        unified_diff=unified,
    )


def diff_to_dict(entry: DiffEntry) -> dict[str, object]:
    """Serialize one DiffEntry for History/API payloads."""
    return {
        "path": entry.path,
        "operation": entry.operation,
        "before_hash": entry.before_hash,
        "after_hash": entry.after_hash,
        "before_size": entry.before_size,
        "after_size": entry.after_size,
        "unified_diff": entry.unified_diff,
        "status": entry.status,
    }


__all__ = ["DiffEntry", "build_diff", "diff_to_dict", "sha256"]
