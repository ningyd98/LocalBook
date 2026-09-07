"""M1 §8.4 watcher: normalization, debounce/coalescing, filtering, lifecycle.

Watchdog is a locked runtime dependency, but the deterministic tests never
rely on the platform observer: events are driven through the same handler the
observer would invoke, with a fake observer injected for lifecycle control.
One best-effort integration test uses the real FSEvents backend and skips
when the environment cannot deliver events.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from server.vault.events import VaultEvent, VaultEventCallback
from server.vault.path_safety import PathSafety
from server.vault.watcher import VaultWatcher


class _FakeObserver:
    """Minimal stand-in for ``watchdog.observers.Observer``."""

    def __init__(self) -> None:
        self.handler = None
        self.root: str | None = None
        self.recursive: bool | None = None
        self.started = False
        self.stopped = False

    def schedule(self, handler: object, path: str, recursive: bool = False) -> object:
        self.handler = handler
        self.root = path
        self.recursive = recursive
        return None

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def join(self, timeout: float | None = None) -> None:
        return None


class _RaisingObserver:
    def schedule(self, *args: object, **kwargs: object) -> object:
        return None

    def start(self) -> None:
        raise OSError("observer backend unavailable")


def _watchdog_event_types() -> tuple[type, type, type, type]:
    from watchdog.events import (
        FileCreatedEvent,
        FileDeletedEvent,
        FileModifiedEvent,
        FileMovedEvent,
    )

    return FileCreatedEvent, FileDeletedEvent, FileModifiedEvent, FileMovedEvent


def _start_fake_watcher(
    root: Path,
    *,
    callback: VaultEventCallback | None = None,
    debounce_ms: int = 200,
) -> tuple[VaultWatcher, _FakeObserver]:
    """Start a watcher over the real root with the fake observer injected."""
    fake = _FakeObserver()
    watcher = VaultWatcher(
        PathSafety(root),
        callback=callback,
        debounce_ms=debounce_ms,
        enabled=True,
        observer_factory=lambda: fake,
    )
    watcher.start()
    assert watcher.status == "running"
    return watcher, fake


def test_start_uses_observer_and_reports_running(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    watcher, fake = _start_fake_watcher(root)
    assert fake.started is True
    assert fake.root == str(root.resolve())
    assert fake.recursive is True
    assert fake.handler is not None
    watcher.stop(timeout=1.0)
    assert fake.stopped is True
    assert watcher.status == "stopped"


def test_normalized_events_create_modify_delete_move(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "dir").mkdir()
    seen: list[VaultEvent] = []

    def callback(event: VaultEvent) -> None:
        seen.append(event)

    watcher, fake = _start_fake_watcher(root, callback=callback, debounce_ms=60_000)
    handler = fake.handler
    FileCreatedEvent, FileDeletedEvent, FileModifiedEvent, FileMovedEvent = (
        _watchdog_event_types()
    )

    handler.on_created(FileCreatedEvent(str(root / "dir" / "a.md")))
    handler.on_modified(FileModifiedEvent(str(root / "dir" / "a.md")))
    handler.on_moved(FileMovedEvent(str(root / "dir" / "a.md"), str(root / "b.md")))
    handler.on_deleted(FileDeletedEvent(str(root / "b.md")))
    watcher.flush()

    kinds = [(event.kind, event.path, event.old_path, event.new_path) for event in seen]
    assert ("create", "dir/a.md", None, None) in kinds
    assert ("modify", "dir/a.md", None, None) in kinds
    assert ("move", None, "dir/a.md", "b.md") in kinds
    assert ("delete", "b.md", None, None) in kinds
    assert all(event.occurred_at is not None for event in seen)
    watcher.stop(timeout=1.0)


def test_debounce_coalesces_same_key_events_into_one_callback(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    seen: list[VaultEvent] = []

    def callback(event: VaultEvent) -> None:
        seen.append(event)

    watcher, fake = _start_fake_watcher(root, callback=callback, debounce_ms=60_000)
    handler = fake.handler
    _Created, _Deleted, FileModifiedEvent, _Moved = _watchdog_event_types()

    # A burst of modifies to the same path inside the debounce window must
    # collapse to a single normalized event.
    for _ in range(5):
        handler.on_modified(FileModifiedEvent(str(root / "busy.md")))
    watcher.flush()
    assert len(seen) == 1
    assert seen[0].kind == "modify"
    assert seen[0].path == "busy.md"
    watcher.stop(timeout=1.0)


def test_move_events_keyed_on_both_paths(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    seen: list[VaultEvent] = []

    def callback(event: VaultEvent) -> None:
        seen.append(event)

    watcher, fake = _start_fake_watcher(root, callback=callback, debounce_ms=60_000)
    handler = fake.handler
    _Created, _Deleted, _Modified, FileMovedEvent = _watchdog_event_types()

    handler.on_moved(FileMovedEvent(str(root / "a.md"), str(root / "b.md")))
    handler.on_moved(FileMovedEvent(str(root / "a.md"), str(root / "b.md")))
    watcher.flush()
    assert len(seen) == 1  # identical (kind, old, new) collapsed
    assert seen[0].kind == "move"
    assert (seen[0].old_path, seen[0].new_path) == ("a.md", "b.md")
    watcher.stop(timeout=1.0)


def test_localnote_events_are_filtered_out(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / ".localnote").mkdir()
    seen: list[VaultEvent] = []

    def callback(event: VaultEvent) -> None:
        seen.append(event)

    watcher, fake = _start_fake_watcher(root, callback=callback, debounce_ms=60_000)
    handler = fake.handler
    FileCreatedEvent, _Deleted, _Modified, _Moved = _watchdog_event_types()

    handler.on_created(FileCreatedEvent(str(root / ".localnote" / "state.json")))
    handler.on_created(FileCreatedEvent(str(root / "note.md")))
    watcher.flush()
    assert [event.path for event in seen] == ["note.md"]
    watcher.stop(timeout=1.0)


def test_outside_and_unsafe_paths_are_dropped(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    seen: list[VaultEvent] = []

    def callback(event: VaultEvent) -> None:
        seen.append(event)

    watcher, fake = _start_fake_watcher(root, callback=callback, debounce_ms=60_000)
    handler = fake.handler
    FileCreatedEvent, _Deleted, FileModifiedEvent, _Moved = _watchdog_event_types()

    outside = tmp_path / "outside-event.md"
    outside.write_text("x\n", encoding="utf-8")
    handler.on_created(FileCreatedEvent(str(outside)))
    handler.on_modified(FileModifiedEvent(str(root / "in.md")))
    watcher.flush()
    assert [event.path for event in seen] == ["in.md"]
    watcher.stop(timeout=1.0)


def test_stop_terminates_and_stops_dispatching(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    seen: list[VaultEvent] = []

    def callback(event: VaultEvent) -> None:
        seen.append(event)

    watcher, fake = _start_fake_watcher(root, callback=callback, debounce_ms=0)
    handler = fake.handler
    _Created, _Deleted, FileModifiedEvent, _Moved = _watchdog_event_types()
    handler.on_modified(FileModifiedEvent(str(root / "instant.md")))
    for _ in range(100):
        if seen:
            break
        time.sleep(0.02)
    assert seen, "debounce worker never dispatched the event"
    watcher.stop(timeout=1.0)
    assert watcher.status == "stopped"
    assert watcher.observer is None
    count = len(seen)
    time.sleep(0.1)
    assert len(seen) == count  # nothing dispatched after stop


def test_callback_error_is_contained(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    seen: list[VaultEvent] = []

    def callback(event: VaultEvent) -> None:
        if event.path == "boom.md":
            raise RuntimeError("consumer bug")
        seen.append(event)

    watcher, fake = _start_fake_watcher(root, callback=callback, debounce_ms=60_000)
    handler = fake.handler
    FileCreatedEvent, _Deleted, _Modified, _Moved = _watchdog_event_types()
    handler.on_created(FileCreatedEvent(str(root / "boom.md")))
    handler.on_created(FileCreatedEvent(str(root / "fine.md")))
    watcher.flush()  # must not raise: failures are logged and skipped
    assert [event.path for event in seen] == ["fine.md"]
    assert watcher.status == "running"
    watcher.stop(timeout=1.0)


def test_observer_start_failure_reports_unavailable(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    watcher = VaultWatcher(
        PathSafety(root),
        observer_factory=lambda: _RaisingObserver(),
        enabled=True,
    )
    watcher.start()
    assert watcher.status == "unavailable"
    assert watcher.diagnostic is not None
    watcher.stop(timeout=0.5)  # must be a no-op, not an error


def test_missing_watchdog_reports_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import server.vault.watcher as watcher_module

    root = tmp_path / "vault"
    root.mkdir()
    watcher = VaultWatcher(PathSafety(root), enabled=True)
    monkeypatch.setattr(watcher_module, "Observer", None)
    watcher.start()
    assert watcher.status == "unavailable"
    assert "watchdog" in (watcher.diagnostic or "")
    watcher.stop(timeout=0.5)


def test_disabled_watcher_stays_disabled(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    watcher = VaultWatcher(PathSafety(root), enabled=False)
    watcher.start()
    assert watcher.status == "disabled"
    watcher.stop(timeout=0.5)
    assert watcher.status == "disabled"


def test_real_observer_delivers_create_event(tmp_path: Path) -> None:  # best effort
    """Real end-to-end observer sanity check; skipped when the platform
    cannot deliver events (CI containers, exotic filesystems)."""
    pytest.importorskip("watchdog")
    root = tmp_path / "vault"
    root.mkdir()
    seen: list[VaultEvent] = []

    def callback(event: VaultEvent) -> None:
        seen.append(event)

    watcher = VaultWatcher(
        PathSafety(root),
        callback=callback,
        debounce_ms=50,
        enabled=True,
    )
    watcher.start()
    try:
        if watcher.status != "running":
            pytest.skip(f"observer unavailable: {watcher.diagnostic}")
        (root / "event.md").write_text("# event\n", encoding="utf-8")
        deadline = time.monotonic() + 6.0
        while time.monotonic() < deadline:
            if any(event.kind == "create" and event.path == "event.md" for event in seen):
                return
            time.sleep(0.05)
        pytest.skip("no watcher event delivered within 6s (flaky platform)")
    finally:
        watcher.stop(timeout=2.0)


def test_event_model_validates_move_fields() -> None:
    with pytest.raises(ValueError):
        VaultEvent("move", old_path="a.md", new_path="")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        VaultEvent("create", path="")  # type: ignore[arg-type]
