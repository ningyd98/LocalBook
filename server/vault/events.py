"""Normalized filesystem event types used by the M1 watcher.

Events are notifications only.  They never contain file contents and never
cause an index update or an automatic write.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol

EventKind = Literal["create", "modify", "delete", "move"]


@dataclass(frozen=True, slots=True)
class VaultEvent:
    kind: EventKind
    path: str | None = None
    old_path: str | None = None
    new_path: str | None = None
    is_directory: bool = False
    occurred_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.occurred_at is None:
            object.__setattr__(self, "occurred_at", datetime.now(UTC))
        if self.kind == "move":
            if not self.old_path or not self.new_path:
                raise ValueError("move events require old_path and new_path")
        elif not self.path:
            raise ValueError("non-move events require path")

    @property
    def key(self) -> tuple[str, ...]:
        if self.kind == "move":
            return (self.kind, self.old_path or "", self.new_path or "")
        return (self.kind, self.path or "")

    @classmethod
    def created(cls, path: str, *, is_directory: bool = False) -> VaultEvent:
        return cls("create", path=path, is_directory=is_directory)

    @classmethod
    def modified(cls, path: str, *, is_directory: bool = False) -> VaultEvent:
        return cls("modify", path=path, is_directory=is_directory)

    @classmethod
    def deleted(cls, path: str, *, is_directory: bool = False) -> VaultEvent:
        return cls("delete", path=path, is_directory=is_directory)

    @classmethod
    def moved(
        cls,
        old_path: str,
        new_path: str,
        *,
        is_directory: bool = False,
    ) -> VaultEvent:
        return cls(
            "move",
            old_path=old_path,
            new_path=new_path,
            is_directory=is_directory,
        )


# Names used by early consumers and by the protocol notes.
NormalizedVaultEvent = VaultEvent


class VaultEventCallback(Protocol):
    def __call__(self, event: VaultEvent) -> None: ...


EventCallback = VaultEventCallback

__all__ = [
    "EventCallback",
    "EventKind",
    "NormalizedVaultEvent",
    "VaultEvent",
    "VaultEventCallback",
]
