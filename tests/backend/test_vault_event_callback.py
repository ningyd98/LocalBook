"""M3-09: VaultService.set_event_callback minimal, backward-compatible add.

PLAN-M3 §5.4 — attaching/replacing the watcher consumer must update an
already-created watcher in place without touching any existing API.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from server.vault.events import VaultEvent, VaultEventCallback
from server.vault.path_safety import PathSafety
from server.vault.service import VaultService
from server.vault.watcher import VaultWatcher


def _watcher_factory(
    safety: PathSafety,
    *,
    callback: VaultEventCallback | None = None,
    debounce_ms: int = 200,
    enabled: bool = False,
) -> VaultWatcher:
    return VaultWatcher(
        safety, callback=callback, debounce_ms=debounce_ms, enabled=enabled
    )


def _callback(marker: list[str]) -> VaultEventCallback:
    def _on_event(event: VaultEvent) -> None:
        marker.append(event.kind)

    return _on_event


def test_set_event_callback_replaces_watcher_callback(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    first: list[str] = []
    second: list[str] = []
    service = VaultService(
        root,
        watcher_enabled=True,
        watcher_factory=_watcher_factory,
        event_callback=_callback(first),
    )
    service.initialize(start_watcher=False)
    assert service.watcher is not None
    assert service.watcher.callback is not None

    service.set_event_callback(_callback(second))
    assert service.watcher.callback is not None
    # dispatch through the watcher routes events to the newest consumer
    service.watcher._dispatch([VaultEvent.created("a.md")])  # type: ignore[attr-defined]
    assert first == []
    assert second == ["create"]
