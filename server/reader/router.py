"""Reader API routes."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request

from .database import ReaderDatabase
from .errors import ReaderError, ReaderErrorCode
from .schemas import (
    AskRequest,
    AskResponse,
    CapabilitiesResponse,
    DeviceStatusResponse,
    PairRequest,
    PairResponse,
    PullSyncResponse,
    PushSyncRequest,
    PushSyncResponse,
    RegisterRequest,
    RegisterResponse,
    SearchRequest,
    SearchResponse,
)
from .service import ReaderService
from .web_search import SearXNGSearchService, WebSearchUnavailable

router = APIRouter(prefix="/api/v1/reader", tags=["reader"])


def get_reader_service(request: Request) -> ReaderService:
    """Get or create Reader service dependency."""
    service = getattr(request.app.state, "reader_service", None)
    if service is not None:
        return service

    # Get database path from settings
    from server.config import Settings

    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        settings = Settings()
        request.app.state.settings = settings

    # Initialize database
    # Use vault root if configured, otherwise use temp directory for testing
    if settings.vault.root:
        db_path = settings.vault.root / ".localnote" / "reader.db"
    else:
        import tempfile

        db_path = Path(tempfile.gettempdir()) / "localbook_reader_test.db"

    # Ensure .localnote directory exists
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # Use absolute path for database connection
    database = ReaderDatabase(str(db_path.absolute()))

    # Get optional AI workflow service
    ai_workflow = None
    try:
        from server.api.dependencies import get_ai_workflow_service

        ai_workflow = get_ai_workflow_service(request)
    except Exception:
        pass  # AI service is optional

    # Get optional search service
    search_service = None
    try:
        from server.api.dependencies import get_search_service

        search_service = get_search_service(request)
    except Exception:
        pass  # Search service is optional

    # Get optional vault service
    vault_service = None
    try:
        from server.api.dependencies import get_vault_service

        vault_service = get_vault_service(request)
    except Exception:
        pass  # Vault service is optional

    web_search_service = None
    if settings.web_search.base_url:
        try:
            web_search_service = SearXNGSearchService(settings.web_search)
        except WebSearchUnavailable:
            logging.getLogger("localnote.reader").warning("Reader web search endpoint is invalid")

    service = ReaderService(
        database, ai_workflow, search_service, vault_service, web_search_service
    )
    request.app.state.reader_service = service
    return service


ReaderServiceDep = Annotated[ReaderService, Depends(get_reader_service)]


def get_device_id_from_token(
    service: ReaderServiceDep,
    authorization: str | None = Header(None),
    reader_token: str | None = Header(None, alias="X-LocalBook-Reader-Token"),
) -> str:
    """Resolve a bearer token through the server-side token registry.

    The token is opaque: no device identity is trusted from its spelling. The
    dependency hashes the presented value and asks the Reader database to return
    the device it was issued for.
    """
    # A public deployment may use HTTP Basic at its reverse proxy while the
    # Reader API still needs a per-device bearer token. The proxy consumes the
    # Authorization header, so the device token can travel in this separate
    # header. On a direct connection, Authorization: Bearer remains supported.
    token = (reader_token or "").strip()
    if not token and authorization:
        parts = authorization.strip().split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            token = parts[1]
    if not token:
        raise ReaderError(
            ReaderErrorCode.UNAUTHORIZED,
            "A Reader access token is required",
            401,
        )

    device_id = service.authenticate_access_token(token)
    if device_id is None:
        raise ReaderError(
            ReaderErrorCode.UNAUTHORIZED,
            "Invalid or revoked access token",
            401,
        )
    return device_id


DeviceIdDep = Annotated[str, Depends(get_device_id_from_token)]


def _bind_authenticated_device_id(body: Any, device_id: str) -> Any:
    """Bind request data to the device identified by the Bearer token.

    Older Reader clients may still include ``device_id`` in AI/search JSON,
    but it is not an authentication input.  Reject a conflicting value and
    always pass the token-derived identity to the service layer.
    """
    request_device_id = getattr(body, "device_id", None)
    if request_device_id is not None and request_device_id != device_id:
        raise ReaderError(
            ReaderErrorCode.UNAUTHORIZED,
            "Authenticated device does not match request",
            401,
        )
    return body.model_copy(update={"device_id": device_id})


# ============================================================================
# Device Pairing Endpoints
# ============================================================================


@router.post("/pair", response_model=PairResponse)
async def pair_device(body: PairRequest, service: ReaderServiceDep) -> PairResponse:
    """Generate a pairing token for device registration.

    The client should display the 6-digit pairing code to the user,
    who then confirms it in the LocalBook UI to authorize the device.
    The token expires after 5 minutes.
    """
    return service.create_pairing_token(body.device_name, body.device_type)


@router.post("/register", response_model=RegisterResponse)
async def register_device(body: RegisterRequest, service: ReaderServiceDep) -> RegisterResponse:
    """Register a device with a pairing token.

    After the user confirms the pairing code in LocalBook UI,
    the client calls this endpoint to complete registration and
    receive an access token for API access.
    """
    # Validate device_id format (UUID, case-insensitive)
    # macOS client uses UUID().uuidString which may be uppercase
    uuid_pattern = re.compile(
        r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
    )
    if not uuid_pattern.match(body.device_id):
        raise ReaderError(
            ReaderErrorCode.INVALID_DEVICE_ID,
            f"device_id must be a valid UUID format, got: {body.device_id}",
            400,
        )

    try:
        return service.register_device(
            body.device_id,
            body.device_name,
            body.device_type,
            body.pairing_token,
            body.public_key,
        )
    except ReaderError:
        raise


@router.get("/devices", response_model=list[DeviceStatusResponse])
async def list_devices(
    _authenticated_device: DeviceIdDep,
    service: ReaderServiceDep,
) -> list[DeviceStatusResponse]:
    """List all registered devices.

    Returns a list of all devices that have been registered,
    including their status and last seen timestamp.
    """
    try:
        return service.list_devices()
    except ReaderError:
        raise


@router.get("/status", response_model=DeviceStatusResponse)
async def device_status(device_id: DeviceIdDep, service: ReaderServiceDep) -> DeviceStatusResponse:
    """Get device status.

    Returns device information including last seen timestamp
    and active status (active if seen within 7 days).
    """
    try:
        return service.get_device_status(device_id)
    except ReaderError:
        raise


# ============================================================================
# Sync Endpoints
# ============================================================================


@router.post("/sync/push", response_model=PushSyncResponse)
async def push_sync(
    body: PushSyncRequest, device_id: DeviceIdDep, service: ReaderServiceDep
) -> PushSyncResponse:
    """Push batch sync operations (1-100 items).

    Accepts create, update, and delete operations for sources, sessions,
    highlights, and notes. Returns counts of accepted/rejected operations
    and any conflicts that occurred.
    """
    try:
        operations = [op.model_dump() for op in body.operations]
        return service.push_sync(device_id, operations)
    except ReaderError:
        raise


@router.get("/sync/pull", response_model=PullSyncResponse)
async def pull_sync(
    device_id: DeviceIdDep,
    service: ReaderServiceDep,
    cursor: str | None = None,
    limit: int = Query(default=100, ge=1, le=100),
) -> PullSyncResponse:
    """Pull incremental sync changes based on cursor.

    Returns changes from other devices that occurred after the provided cursor.
    For initial sync, omit the cursor parameter. The response includes a
    next_cursor for pagination and has_more flag.
    """
    try:
        return service.pull_sync(device_id, cursor, limit)
    except ReaderError:
        raise


@router.get("/capabilities", response_model=CapabilitiesResponse)
async def get_capabilities(service: ReaderServiceDep) -> CapabilitiesResponse:
    """Get server capabilities.

    Returns version, max batch size, supported source types,
    and feature flags.
    """
    return service.get_capabilities()


# ============================================================================
# AI Query Endpoints
# ============================================================================


@router.post("/ask", response_model=AskResponse)
async def ask_ai(
    body: AskRequest, device_id: DeviceIdDep, service: ReaderServiceDep
) -> AskResponse:
    """AI query with context.

    Supports conversational queries with optional source, highlight,
    and conversation context. Returns AI answer with citations.
    """
    body = _bind_authenticated_device_id(body, device_id)
    try:
        return await service.ask(body)
    except ReaderError:
        raise


@router.post("/search", response_model=SearchResponse)
async def search(
    body: SearchRequest, device_id: DeviceIdDep, service: ReaderServiceDep
) -> SearchResponse:
    """Cross-Reader+LocalBook semantic search.

    Searches across reader sources, highlights, notes, and LocalBook notes.
    Returns ranked results with snippets and metadata.
    """
    body = _bind_authenticated_device_id(body, device_id)
    try:
        return await service.search(body)
    except ReaderError:
        raise


@router.post("/sessions/{session_id}/import")
async def import_session(
    session_id: str, device_id: DeviceIdDep, service: ReaderServiceDep
) -> dict:
    """Import a Reader session as a Markdown document into LocalBook vault.

    Converts the session (highlights, notes, AI conversations) into a structured
    Markdown document and writes it to the vault at Reading/YYYY/MM/title.md.

    The document is automatically indexed for full-text search and vector search.

    Args:
        session_id: UUID of the session to import

    Returns:
        dict with document metadata:
            - path: relative path in vault
            - sha256: content hash
            - byte_length: document size in bytes
            - operation: "created"

    Raises:
        404: session not found
        503: vault service not available
    """
    try:
        result = service.import_session(session_id, device_id=device_id)
        return result
    except ReaderError:
        raise
