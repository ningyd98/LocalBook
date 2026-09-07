"""LocalNote FastAPI application and M1 lifecycle wiring."""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ..ai.errors import AIError
from ..config import Settings
from ..policies.errors import M7Error
from ..scheduler.errors import SchedulerError
from ..vault.errors import VaultError, VaultErrorCode
from ..vault.lifecycle import VaultLifecycle
from .routes import ai as ai_routes
from .routes import graph as graph_routes
from .routes import health as health_routes
from .routes import history as history_routes
from .routes import index as index_routes
from .routes import jobs as jobs_routes
from .routes import links as links_routes
from .routes import metadata as metadata_routes
from .routes import scheduler as scheduler_routes
from .routes import search as search_routes
from .routes import vault as vault_routes

logger = logging.getLogger("localnote.api")


def _validation_error_payload(exc: RequestValidationError) -> tuple[int, dict[str, Any]]:
    """Turn Pydantic input failures into a safe, compact API error body.

    Mappings (PLAN-M1 §4.6): path-shaped attacks → 400 ``path_traversal``;
    malformed hash/base64 values → 400 ``invalid_request``; every other
    schema failure → 422 ``invalid_request``.  Values are never echoed for
    content/hash fields.
    """
    errors = exc.errors()
    joined = " ".join(str(item.get("msg", "Invalid request")) for item in errors).lower()
    locations = [item.get("loc", ()) for item in errors]
    path_related = any(
        any(str(part) in {"path", "source_path", "destination_path"} for part in loc)
        for loc in locations
    )
    hash_content_related = any(
        any(str(part) in {"expected_sha256", "sha256", "content_base64"} for part in loc)
        for loc in locations
    )
    traversal_related = any(
        token in joined
        for token in ("unsafe", "root-relative", "traversal", "drive", "unc", "separator")
    )
    if path_related and traversal_related:
        status_code, code, message = 400, VaultErrorCode.PATH_TRAVERSAL.value, (
            "Path must be a safe root-relative POSIX path"
        )
    elif hash_content_related or any(token in joined for token in ("digest", "base64")):
        status_code, code, message = 400, VaultErrorCode.INVALID_REQUEST.value, "Invalid request"
    else:
        status_code, code, message = 422, VaultErrorCode.INVALID_REQUEST.value, "Invalid request"
    path: str | None = None
    if status_code == 400 and code == VaultErrorCode.PATH_TRAVERSAL.value:
        for item in errors:
            ctx = item.get("input")
            if isinstance(ctx, str) and any(
                str(part) in {"path", "source_path", "destination_path"}
                for part in item.get("loc", ())
            ):
                # Only echo a short relative-looking value; malformed values
                # such as absolute paths are intentionally omitted.
                if not ctx.startswith(("/", "\\")) and "\x00" not in ctx and len(ctx) <= 512:
                    path = ctx
                break
    return status_code, {"error": {"code": code, "message": message, "path": path}}


def _vault_error_response(exc: VaultError) -> JSONResponse:
    status_by_code = {
        VaultErrorCode.VAULT_NOT_CONFIGURED: 503,
        VaultErrorCode.VAULT_UNAVAILABLE: 503,
        VaultErrorCode.WATCHER_UNAVAILABLE: 503,
        VaultErrorCode.PATH_TRAVERSAL: 400,
        VaultErrorCode.SYMLINK_ESCAPE: 400,
        VaultErrorCode.INVALID_REQUEST: 400,
        VaultErrorCode.NOT_A_FILE: 400,
        VaultErrorCode.NOT_A_DIRECTORY: 400,
        VaultErrorCode.NOT_FOUND: 404,
        VaultErrorCode.ALREADY_EXISTS: 409,
        VaultErrorCode.FILE_CONFLICT: 409,
        VaultErrorCode.EXPECTED_HASH_REQUIRED: 400,
        VaultErrorCode.FILE_TOO_LARGE: 413,
        VaultErrorCode.ATOMIC_WRITE_FAILED: 500,
        VaultErrorCode.INDEX_UNAVAILABLE: 503,
        VaultErrorCode.INTERNAL_ERROR: 500,
    }
    status_code = status_by_code.get(exc.code, 500)
    # Domain messages are authored as safe fixed strings.  Keep the optional
    # path relative-only; never return the configured absolute root.
    path = exc.path if isinstance(exc.path, str) and not exc.path.startswith(("/", "\\")) else None
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": exc.code.value, "message": exc.message, "path": path}},
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create an app with one settings snapshot and one Vault lifecycle owner."""
    effective_settings = settings or Settings()

    @asynccontextmanager
    async def app_lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Settings construction and Vault initialization are isolated: a
        # missing/nonexistent root records a 503 domain state but does not stop
        # health or AI status from starting.
        app.state.settings = effective_settings
        lifecycle = VaultLifecycle(
            effective_settings.vault,
            note_text_cap=effective_settings.index.note_text_cap,
            index=effective_settings.index,
        )
        app.state.vault_lifecycle = lifecycle
        service = lifecycle.startup()
        vault_service = None
        if service is not None:
            app.state.vault_service = service
            vault_service = service
        if lifecycle.index_service is not None:
            app.state.index_service = lifecycle.index_service
        logger.info(
            "localnote api starting host=%s port=%s vault_configured=%s "
            "ai_configured=%s index_state=%s",
            effective_settings.server.host,
            effective_settings.server.port,
            effective_settings.vault.root is not None,
            effective_settings.ai.base_url not in (None, ""),
            lifecycle.index_service.build_state
            if lifecycle.index_service is not None
            else "n/a",
        )
        # M8: derive History/Agent services + one scheduler singleton.  A
        # scheduler that fails to start degrades (status ``running=false``)
        # and never blocks health/Vault/editor/AI/manual jobs.
        scheduler = None
        try:
            from .dependencies import (
                _agent_service_from,
                _history_from_index,
                build_scheduler_service,
            )

            history = _history_from_index(lifecycle.index_service)
            agent_service = None
            if vault_service is not None:
                agent_service = _agent_service_from(
                    effective_settings, vault_service, lifecycle.index_service, history
                )
                app.state.agent_job_service = agent_service
            scheduler = build_scheduler_service(
                effective_settings,
                vault=vault_service,
                index=lifecycle.index_service,
                agent_service=agent_service,
                history=history,
            )
            # Startup crash scan flags interrupted derived rows (jobs and
            # scheduler runs) as recovery_required — diagnostics only, never
            # an automatic write/rollback — regardless of whether the
            # scheduler is enabled this run.
            scan_result = scheduler.scan_recovery(mark=True)
            if (
                scan_result.get("marked_jobs", 0)
                or scan_result.get("marked_runs", 0)
            ):
                logger.warning(
                    "startup recovery scan flagged %s job(s) / %s run(s) "
                    "as recovery_required",
                    scan_result.get("marked_jobs", 0),
                    scan_result.get("marked_runs", 0),
                )
            app.state.scheduler_service = scheduler
            try:
                scheduler.start()
            except Exception:
                logger.exception("scheduler failed to start; status will report degraded")
            if scheduler.network_warning():
                logger.warning(
                    "LOCALNOTE_HOST is not loopback: the API has no auth/HTTPS; "
                    "any device on the LAN can call write endpoints. "
                    "Firewall the port (PLAN-M8 §5.8)."
                )
                advice = scheduler.network_advice()
                if advice is not None:
                    logger.warning("network exposure advice: %s", advice)
        except Exception:
            logger.exception("scheduler construction failed; continuing without it")
        try:
            yield
        finally:
            if scheduler is not None:
                try:
                    scheduler.stop(wait=True, timeout=3.0)
                except Exception:
                    logger.exception("scheduler shutdown encountered an error")
            lifecycle.shutdown()
            for attr in (
                "vault_service",
                "index_service",
                "agent_job_service",
                "scheduler_service",
            ):
                if hasattr(app.state, attr):
                    delattr(app.state, attr)

    app = FastAPI(
        title="LocalNote Server",
        version="0.2.0",
        description=(
            "Markdown-first local note server (M1 Vault core + M3 "
            "metadata/links/search read layer on the M4 SQLite derived index)."
        ),
        lifespan=app_lifespan,
    )
    # Make settings available for TestClient calls that do not enter the
    # lifespan context.  This does not touch the filesystem.
    app.state.settings = effective_settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=effective_settings.server.cors_origins,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        allow_credentials=False,
        max_age=600,
    )

    app.include_router(health_routes.router)
    app.include_router(ai_routes.router)
    app.include_router(vault_routes.router)
    app.include_router(metadata_routes.router)
    app.include_router(links_routes.router)
    app.include_router(search_routes.router)
    app.include_router(index_routes.router)
    app.include_router(graph_routes.router)
    app.include_router(jobs_routes.router)
    app.include_router(history_routes.router)
    app.include_router(scheduler_routes.router)

    @app.middleware("http")
    async def log_requests(request: Request, call_next):  # type: ignore[no-untyped-def]
        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000.0
        if request.url.path != "/api/v1/health":
            logger.info(
                "request method=%s path=%s status=%d duration_ms=%.1f",
                request.method,
                request.url.path,
                response.status_code,
                duration_ms,
            )
        return response

    @app.exception_handler(M7Error)
    async def m7_error_handler(request: Request, exc: M7Error) -> JSONResponse:
        """M7 domain errors: fixed code/message, relative path, extra meta."""
        logger.info(
            "m7 error method=%s path=%s code=%s",
            request.method,
            request.url.path,
            exc.code.value,
        )
        content = {
            "error": {
                "code": exc.code.value,
                "message": exc.message,
                "path": exc.path,
            },
            "meta": exc.meta,
        }
        return JSONResponse(status_code=exc.status_code, content=content)

    @app.exception_handler(SchedulerError)
    async def scheduler_error_handler(request: Request, exc: SchedulerError) -> JSONResponse:
        """M8 scheduler domain errors: fixed code/message + meta only."""
        logger.info(
            "scheduler error method=%s path=%s code=%s",
            request.method,
            request.url.path,
            exc.code.value,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code.value,
                    "message": exc.message,
                    "path": None,
                },
                "meta": exc.meta,
            },
        )

    @app.exception_handler(AIError)
    async def ai_error_handler(request: Request, exc: AIError) -> JSONResponse:
        content = {
            "error": {
                "code": exc.code.value,
                "message": exc.message,
                "path": None,
            },
            "meta": exc.meta,
        }
        return JSONResponse(status_code=exc.status_code, content=content)

    @app.exception_handler(VaultError)
    async def vault_error_handler(request: Request, exc: VaultError) -> JSONResponse:
        logger.info(
            "vault error method=%s path=%s code=%s",
            request.method,
            request.url.path,
            exc.code.value,
        )
        return _vault_error_response(exc)

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        status_code, payload = _validation_error_payload(exc)
        logger.info(
            "request validation method=%s path=%s status=%d",
            request.method,
            request.url.path,
            status_code,
        )
        return JSONResponse(status_code=status_code, content=payload)

    @app.exception_handler(Exception)
    async def safe_unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        # Never leak a Python traceback, configured root, request body, or
        # exception details to an API client.
        logger.exception("unhandled error method=%s path=%s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": VaultErrorCode.INTERNAL_ERROR.value,
                    "message": "Internal server error",
                    "path": None,
                }
            },
        )

    return app


app = create_app()
