"""Optional watchdog adapter with bounded, debounced normalized events."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .errors import VaultError
from .events import VaultEvent, VaultEventCallback
from .path_safety import PathSafety, is_reserved_derived_path, validate_relative_path

logger = logging.getLogger("localnote.vault.watcher")

try:  # watchdog is intentionally optional so Vault I/O remains usable.
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
except ImportError:  # pragma: no cover - exercised in dependency-minimal envs
    FileSystemEventHandler = object  # type: ignore[assignment,misc]
    Observer = None  # type: ignore[assignment,misc]


class _WatchdogHandler(FileSystemEventHandler):
    def __init__(self, owner: VaultWatcher) -> None:
        self.owner = owner

    def on_created(self, event: Any) -> None:
        self.owner.emit_created(
            getattr(event, "src_path", ""), is_directory=bool(event.is_directory)
        )

    def on_modified(self, event: Any) -> None:
        self.owner.emit_modified(
            getattr(event, "src_path", ""), is_directory=bool(event.is_directory)
        )

    def on_deleted(self, event: Any) -> None:
        self.owner.emit_deleted(
            getattr(event, "src_path", ""), is_directory=bool(event.is_directory)
        )

    def on_moved(self, event: Any) -> None:
        self.owner.emit_moved(
            getattr(event, "src_path", ""),
            getattr(event, "dest_path", ""),
            is_directory=bool(event.is_directory),
        )


class VaultWatcher:
    """Watch a Vault root and dispatch safe, debounced notifications.

    ``status`` is one of ``disabled``, ``stopped``, ``running`` or
    ``unavailable``.  A missing watchdog installation never prevents the
    service from reading/writing files; it simply leaves this adapter
    ``unavailable`` and records a safe diagnostic.
    """

    def __init__(
        self,
        safety: PathSafety,
        *,
        callback: VaultEventCallback | None = None,
        debounce_ms: int = 200,
        enabled: bool = True,
        observer_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.safety = safety
        self.callback = callback
        self.debounce_ms = max(0, int(debounce_ms))
        self.enabled = enabled
        self._observer_factory = observer_factory
        self._observer: Any | None = None
        self._pending: dict[tuple[str, ...], tuple[float, VaultEvent]] = {}
        self._condition = threading.Condition()
        self._stop_requested = False
        self._worker: threading.Thread | None = None
        self._status = "disabled" if not enabled else "stopped"
        self._diagnostic: str | None = None

    @property
    def status(self) -> str:
        return self._status

    @property
    def diagnostic(self) -> str | None:
        return self._diagnostic

    @property
    def observer(self) -> Any | None:
        return self._observer

    def start(self) -> None:
        if not self.enabled:
            self._status = "disabled"
            return
        if self._status == "running":
            return
        factory = self._observer_factory or Observer
        if factory is None:
            self._status = "unavailable"
            self._diagnostic = "watchdog is not installed"
            logger.warning("vault watcher unavailable reason=watchdog_missing")
            return
        try:
            observer = factory()
            handler = _WatchdogHandler(self)
            observer.schedule(handler, str(self.safety.root), recursive=True)
            observer.start()
            self._observer = observer
            self._stop_requested = False
            self._worker = threading.Thread(
                target=self._dispatch_loop,
                name="localnote-vault-events",
                daemon=True,
            )
            self._worker.start()
            self._status = "running"
            self._diagnostic = None
        except Exception as exc:  # adapter failure must not disable Vault I/O
            self._status = "unavailable"
            self._diagnostic = "watcher could not be started"
            self._observer = None
            logger.warning(
                "vault watcher unavailable reason=start_failure type=%s", type(exc).__name__
            )

    def stop(self, timeout: float = 2.0) -> None:
        observer = self._observer
        if observer is not None:
            try:
                observer.stop()
            except Exception:
                logger.warning("vault watcher observer stop failed")
        with self._condition:
            self._stop_requested = True
            self._condition.notify_all()
        worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(timeout=max(0.0, timeout))
        if observer is not None:
            try:
                observer.join(timeout=max(0.0, timeout))
            except TypeError:
                try:
                    observer.join()
                except Exception:
                    pass
            except Exception:
                logger.warning("vault watcher observer join failed")
        self._observer = None
        self._worker = None
        if self.enabled and self._status != "unavailable":
            self._status = "stopped"

    def flush(self) -> None:
        """Synchronously dispatch all currently pending events."""
        while True:
            with self._condition:
                if not self._pending:
                    return
                events = [event for _, event in self._pending.values()]
                self._pending.clear()
            self._dispatch(events)

    def _dispatch_loop(self) -> None:
        while True:
            with self._condition:
                if self._stop_requested:
                    events = [event for _, event in self._pending.values()]
                    self._pending.clear()
                    should_stop = True
                else:
                    now = time.monotonic()
                    due = [
                        event
                        for deadline, event in self._pending.values()
                        if deadline <= now
                    ]
                    for event in due:
                        self._pending.pop(event.key, None)
                    if not due:
                        wait_for = None
                        if self._pending:
                            earliest = min(
                                self._pending.values(), key=lambda item: item[0]
                            )[0]
                            wait_for = max(0.001, earliest - now)
                        self._condition.wait(timeout=wait_for)
                        continue
                    events = due
                    should_stop = False
            self._dispatch(events)
            if should_stop:
                return

    def _dispatch(self, events: list[VaultEvent]) -> None:
        if self.callback is None:
            return
        for event in events:
            try:
                self.callback(event)
            except Exception:
                # A consumer callback must never kill the observer/dispatcher.
                logger.exception("vault watcher callback failed kind=%s", event.kind)

    def _normalize_os_path(self, raw_path: str) -> str | None:
        if not raw_path:
            return None
        try:
            path = Path(raw_path)
            # Watchdog supplies absolute paths.  Reject paths that cannot be
            # proven to be beneath the immutable real root.
            real = path.resolve(strict=False)
            real.relative_to(self.safety.root)
            relative = self.safety.root.joinpath(*path.relative_to(self.safety.root).parts)
            display = self.safety.display_path(relative)
            if is_reserved_derived_path(display):
                return None
            validate_relative_path(display)
            return display
        except (VaultError, ValueError, OSError):
            # Root-directory events (display "."), reserved/unsafe displays and
            # anything that fails containment are dropped, never dispatched.
            return None

    def _queue(self, event: VaultEvent) -> None:
        with self._condition:
            deadline = time.monotonic() + self.debounce_ms / 1000.0
            self._pending[event.key] = (deadline, event)
            self._condition.notify_all()

    def emit_created(self, raw_path: str, *, is_directory: bool = False) -> None:
        path = self._normalize_os_path(raw_path)
        if path is not None:
            self._queue(VaultEvent.created(path, is_directory=is_directory))

    def emit_modified(self, raw_path: str, *, is_directory: bool = False) -> None:
        path = self._normalize_os_path(raw_path)
        if path is not None:
            self._queue(VaultEvent.modified(path, is_directory=is_directory))

    def emit_deleted(self, raw_path: str, *, is_directory: bool = False) -> None:
        path = self._normalize_os_path(raw_path)
        if path is not None:
            self._queue(VaultEvent.deleted(path, is_directory=is_directory))

    def emit_moved(
        self,
        raw_old_path: str,
        raw_new_path: str,
        *,
        is_directory: bool = False,
    ) -> None:
        old_path = self._normalize_os_path(raw_old_path)
        new_path = self._normalize_os_path(raw_new_path)
        if old_path is not None and new_path is not None:
            self._queue(VaultEvent.moved(old_path, new_path, is_directory=is_directory))


__all__ = ["VaultWatcher"]
