"""Application lifecycle wiring for the Vault service, index and watcher.

Startup order is fixed by PLAN-M3 §5.3/§5.4 and extended by PLAN-M4 §5.7:

1. create the ``VaultService`` without starting its watcher;
2. initialise the deletable ``.localnote`` derived state (``state.json``);
3. open/migrate ``.localnote/index.db`` (``DerivedIndexService`` opens the
   SQLite derived database — M4);
4. build the index (full scan inside one transaction) — the index must be
   queryable before any watcher event can flow into it;
5. attach ``index.handle_event`` via ``VaultService.set_event_callback``;
6. start the watcher.

Shutdown stops the watcher first and then closes the SQLite connection so no
watcher thread can touch a closed database.

A missing root disables Vault normally.  A configured root that cannot be
opened is retained as a safe domain error so Vault endpoints return 503, while
health and unrelated API routes continue to start.  An index build failure
only marks the index unavailable (503 on metadata/links/search); Vault I/O and
editing are never blocked by it.
"""

from __future__ import annotations

import logging
from typing import Any

from ..config import IndexSettings, RagSettings, VaultSettings
from ..index.service import DerivedIndexService
from .errors import VaultError, VaultNotConfigured, VaultUnavailable
from .service import VaultService

logger = logging.getLogger("localnote.vault.lifecycle")

_DEFAULT_NOTE_TEXT_CAP = 1_000_000


class VaultLifecycle:
    """Own one optional :class:`VaultService` and its derived index."""

    def __init__(
        self,
        settings: VaultSettings,
        *,
        note_text_cap: int = _DEFAULT_NOTE_TEXT_CAP,
        index: IndexSettings | None = None,
        rag: RagSettings | None = None,
    ) -> None:
        self.settings = settings
        self.index_settings = index or IndexSettings()
        self.rag_settings = rag
        self.service: VaultService | None = None
        self.error: VaultError | None = None
        self.index_service: DerivedIndexService | None = None
        # M14: additive derived layer over the same index.db.  ``rag_stack``
        # stays None when RAG is disabled, when the derived database is
        # unavailable, or when no embedding provider could be built.
        self.rag_stack = None
        self._note_text_cap = note_text_cap

    def startup(self, *, start_watcher: bool = True) -> VaultService | None:
        if self.settings.root is None:
            self.service = None
            self.error = VaultNotConfigured()
            self.index_service = None
            return None
        try:
            service = VaultService.from_settings(self.settings)
            # Watcher is created but deliberately NOT started until the index
            # has been built (PLAN-M3 §5.3 data flow).
            service.initialize(start_watcher=False)
        except VaultError as exc:
            self.service = None
            self.error = exc
            self.index_service = None
            logger.warning("vault unavailable code=%s", exc.code.value)
            return None
        except Exception:
            self.service = None
            self.error = VaultUnavailable("Vault could not be initialized")
            self.index_service = None
            logger.exception("vault initialization failed")
            return None
        self.service = service
        self.error = None

        index_settings = self.index_settings
        index = DerivedIndexService(
            service,
            note_text_cap=self._note_text_cap,
            db_filename=index_settings.db_filename,
            fts_tokenizer=index_settings.fts_tokenizer,
            journal_mode=index_settings.journal_mode,
            synchronous=index_settings.synchronous,
            busy_timeout_ms=index_settings.busy_timeout_ms,
        )
        self.index_service = index
        try:
            index.rebuild()
        except Exception:
            # A scan failure leaves the index "unavailable" (503 on the M3
            # read endpoints) and never disables Vault reads/writes.
            logger.exception("derived index startup scan failed")
        self._start_rag(service, index)
        try:
            service.set_event_callback(self._event_callback(index))
            if start_watcher:
                service.start()
        except Exception:
            logger.exception("vault watcher start failed")
        logger.info(
            "vault initialized watcher_status=%s index_state=%s db=%s",
            service.watcher_status,
            index.build_state,
            index.db_path.name if index.db_path is not None else "n/a",
        )
        return service

    def _start_rag(self, service: VaultService, index: DerivedIndexService) -> None:
        """Build the M14 RAG stack over the shared derived database.

        RAG is strictly additive derived data: any failure here logs and leaves
        ``rag_stack = None`` so Vault editing, FTS search and the API keep
        working exactly as they did before M14.
        """
        settings = self.rag_settings
        if settings is None or not settings.enabled:
            return
        database = index.database
        if database is None or not database.opened:
            logger.info("rag stack skipped: derived database is unavailable")
            return
        try:
            from ..rag.factory import create_rag_stack

            self.rag_stack = create_rag_stack(
                service, database, settings, index=index
            )
        except Exception:
            self.rag_stack = None
            logger.exception("rag stack initialization failed")
            return
        if bool(getattr(settings, "index_on_startup", False)):
            try:
                self.rag_stack.index.rebuild()
            except Exception:
                logger.exception("rag startup indexing failed")

    def reconfigure_rag(self, settings: RagSettings | None) -> None:
        """Swap the RAG stack for a new configuration without touching the Vault.

        Editing RAG settings must not restart the Vault, rebuild the M4 index or
        interrupt editing: only the derived RAG layer is torn down and rebuilt.
        The event callback is re-attached so the watcher keeps feeding both
        indexes.
        """
        service = self.service
        if service is None:
            self.rag_settings = settings
            return
        old = self.rag_stack
        self.rag_settings = settings
        self.rag_stack = None
        index = self.index_service
        try:
            if index is not None:
                self._start_rag(service, index)
        finally:
            if old is not None:
                try:
                    old.close()
                except Exception:  # pragma: no cover - defensive
                    logger.exception("old rag stack cleanup failed")
        if index is not None:
            service.set_event_callback(self._event_callback(index))

    def _event_callback(self, index: DerivedIndexService):
        """Fan one watcher event out to the M4 index and the M14 RAG index."""
        rag_stack = self.rag_stack
        if rag_stack is None:
            return index.handle_event

        def callback(event) -> None:
            index.handle_event(event)
            try:
                rag_stack.index.handle_event(event)
            except Exception:  # pragma: no cover - watcher thread must not die
                logger.exception("rag incremental indexing failed kind=%s", event.kind)

        return callback

    def shutdown(self) -> None:
        service = self.service
        if service is None:
            return
        try:
            service.stop(timeout=2.0)
        except Exception:
            # Shutdown must not prevent the ASGI process from closing; service
            # methods already make a best effort to flush/join the watcher.
            logger.exception("vault shutdown encountered an error")
        finally:
            if self.rag_stack is not None:
                try:
                    self.rag_stack.close()
                except Exception:
                    logger.exception("rag shutdown encountered an error")
                self.rag_stack = None
            if self.index_service is not None:
                try:
                    self.index_service.clear()
                finally:
                    self.index_service.close()
                self.index_service = None
            self.service = None


async def startup_vault(app: Any, settings: VaultSettings) -> VaultService | None:
    """Convenience hook for ASGI lifespan/tests."""
    lifecycle = VaultLifecycle(settings)
    app.state.vault_lifecycle = lifecycle
    return lifecycle.startup()


async def shutdown_vault(app: Any) -> None:
    lifecycle = getattr(app.state, "vault_lifecycle", None)
    if lifecycle is not None:
        lifecycle.shutdown()


__all__ = ["VaultLifecycle", "shutdown_vault", "startup_vault"]
