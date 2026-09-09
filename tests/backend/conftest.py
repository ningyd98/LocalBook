"""Shared backend fixtures and helpers (Phase 0 + M1).

Rules enforced here:
- every test runs with a clean ``LOCALNOTE_*`` environment;
- AI probing never touches a real oMLX server: tests inject
  ``httpx.MockTransport`` handlers or fake clients;
- Vault tests only ever touch fabricated fixtures under
  ``tests/fixtures/vault`` (copied into ``tmp_path``) or fresh throwaway
  directories inside ``tmp_path`` — never a real user Vault;
- Vault routes are wired through FastAPI's ``dependency_overrides`` for the
  ``get_vault_service`` dependency.
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from tests.backend.client import TestClient

from server.ai.service import AIStatusService
from server.api import dependencies
from server.api.main import create_app
from server.index.service import DerivedIndexService
from server.vault.service import VaultService

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"
VAULT_FIXTURES_DIR = FIXTURES_DIR / "vault"


@pytest.fixture(autouse=True)
def _clean_localnote_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Isolate every test from developer/CI ``LOCALNOTE_*`` variables.

    ``LOCALNOTE_SETTINGS_FILE`` is pointed at a per-test path as well: without
    it ``ConfigRepository`` would load the developer's real instance file
    (``~/.config/localnote/instances/<host>-<port>/settings.json``) and a
    configured vault would leak into the "unconfigured vault" contracts.
    """
    for key in list(os.environ):
        if key.startswith("LOCALNOTE_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("LOCALNOTE_SETTINGS_FILE", str(tmp_path / "settings.json"))
    yield


@pytest.fixture
def load_json() -> Callable[[str], object]:
    def _load(name: str) -> object:
        return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))

    return _load


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture
def safe_client() -> TestClient:
    """TestClient that surfaces 500 bodies instead of re-raising exceptions."""
    return TestClient(create_app(), raise_server_exceptions=False)


def make_ai_service(
    handler: Callable[[httpx.Request], httpx.Response] | None = None,
    *,
    base_url: str | None = "http://127.0.0.1:8000/v1",
    **kwargs: object,
) -> AIStatusService:
    """Build an AIStatusService with an optional MockTransport handler."""
    transport: httpx.MockTransport | None = (
        httpx.MockTransport(handler) if handler is not None else None
    )
    return AIStatusService(base_url=base_url, transport=transport, **kwargs)  # type: ignore[arg-type]


@pytest.fixture
def override_ai() -> Callable[[TestClient, AIStatusService], Iterator[None]]:
    """Context manager overriding the route's AI dependency with a service."""

    @contextmanager
    def _override(target: TestClient, service: AIStatusService) -> Iterator[None]:
        target.app.dependency_overrides[dependencies.get_ai_status_service] = (
            lambda: service
        )
        try:
            yield
        finally:
            target.app.dependency_overrides.clear()

    return _override


# --------------------------------------------------------------------------
# M1 Vault fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def vault_fixture_copy(tmp_path: Path) -> Path:
    """Byte-identical copy of ``tests/fixtures/vault`` inside tmp_path."""
    destination = tmp_path / "vault-copy"
    shutil.copytree(VAULT_FIXTURES_DIR, destination, symlinks=True)
    return destination


@pytest.fixture
def vault_service_factory() -> Callable[..., VaultService]:
    """Build a VaultService over a caller-owned root directory.

    Tests are allowed to create the throwaway root and any fixture files
    (including symlinks) inside it first.  Watchers are off by default so no
    observer threads leak between tests.
    """

    def _factory(
        root: str | os.PathLike[str],
        *,
        max_file_bytes: int = 50 * 1024 * 1024,
        watcher_enabled: bool = False,
        watcher_debounce_ms: int = 200,
        initialize: bool = True,
        start_watcher: bool = False,
        event_callback: Any = None,
        watcher_factory: Callable[..., Any] | None = None,
        before_replace_hook: Callable[[Path], None] | None = None,
    ) -> VaultService:
        service = VaultService(
            Path(root),
            max_file_bytes=max_file_bytes,
            watcher_enabled=watcher_enabled,
            watcher_debounce_ms=watcher_debounce_ms,
            event_callback=event_callback,  # type: ignore[arg-type]
            watcher_factory=watcher_factory,
            before_replace_hook=before_replace_hook,
        )
        if initialize:
            service.initialize(start_watcher=start_watcher)
        return service

    return _factory


@pytest.fixture
def vault_service(
    vault_service_factory: Callable[..., VaultService],
    tmp_path: Path,
) -> VaultService:
    """Fresh initialized service on an empty throwaway Vault root."""
    root = tmp_path / "vault"
    root.mkdir()
    return vault_service_factory(root)


@pytest.fixture
def override_vault() -> Callable[[TestClient, VaultService], Iterator[None]]:
    """Context manager overriding the vault route dependency with a service."""

    @contextmanager
    def _override(target: TestClient, service: VaultService) -> Iterator[None]:
        target.app.dependency_overrides[dependencies.get_vault_service] = (
            lambda: service
        )
        try:
            yield
        finally:
            target.app.dependency_overrides.clear()

    return _override


@pytest.fixture
def vault_api_client() -> Callable[..., TestClient]:
    """Factory for TestClients used by the /api/v1/vault integration tests.

    With a service argument the ``get_vault_service`` dependency is replaced
    via FastAPI's override mechanism (the only injection channel used by the
    tests).  Without one the app keeps its default (vault not configured)
    state, which is what the 503 tests exercise.
    """

    def _factory(
        service: VaultService | None = None,
        *,
        settings: Any = None,
    ) -> TestClient:
        app = create_app(settings=settings)
        test_client = TestClient(app, raise_server_exceptions=False)
        if service is not None:
            app.dependency_overrides[dependencies.get_vault_service] = (
                lambda: service
            )
        return test_client

    return _factory


# --------------------------------------------------------------------------
# M3 derived index fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def index_service_factory() -> Callable[..., DerivedIndexService]:
    """Build a rebuilt in-memory index over a caller-owned VaultService."""

    def _factory(
        vault: VaultService,
        *,
        note_text_cap: int = 1_000_000,
    ) -> DerivedIndexService:
        index = DerivedIndexService(vault, note_text_cap=note_text_cap)
        result = index.rebuild()
        assert result.ready is True
        return index

    return _factory


@pytest.fixture
def override_m3() -> Callable[..., Iterator[None]]:
    """Override the M3 service dependencies of a TestClient.

    Replaces the vault + index + metadata/links/search service factories so
    integration tests never depend on app lifespan or settings.
    """

    @contextmanager
    def _override(
        target: TestClient,
        vault: VaultService,
        index: DerivedIndexService,
    ) -> Iterator[None]:
        from server.links.service import LinksService
        from server.metadata.service import MetadataService
        from server.search.service import SearchService

        target.app.dependency_overrides[dependencies.get_vault_service] = (
            lambda: vault
        )
        target.app.dependency_overrides[dependencies.get_index_service] = (
            lambda: index
        )
        target.app.dependency_overrides[dependencies.get_metadata_service] = (
            lambda: MetadataService(vault)
        )
        target.app.dependency_overrides[dependencies.get_links_service] = (
            lambda: LinksService(index)
        )
        target.app.dependency_overrides[dependencies.get_search_service] = (
            lambda: SearchService(index)
        )
        try:
            yield
        finally:
            target.app.dependency_overrides.clear()

    return _override


@pytest.fixture
def configured_settings() -> Callable[[Path], Any]:
    """Settings snapshot pointing Vault at an existing (or missing) root."""

    def _factory(root: str | os.PathLike[str]) -> Any:
        from server.config import Settings, VaultSettings

        return Settings(vault=VaultSettings(root=Path(root), watcher_enabled=False))

    return _factory
