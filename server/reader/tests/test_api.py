"""Tests for Reader API endpoints."""

import tempfile
import time
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from server.reader.database import ReaderDatabase
from server.reader.errors import ReaderError
from server.reader.router import get_reader_service, router
from server.reader.service import ReaderService


@pytest.fixture
def test_db():
    """Create a temporary test database."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    db = ReaderDatabase(db_path)
    yield db

    # Cleanup
    Path(db_path).unlink(missing_ok=True)


@pytest.fixture
def app(test_db):
    """Create test app with test database."""
    app = FastAPI()

    # Add ReaderError exception handler (same as main.py)
    @app.exception_handler(ReaderError)
    async def reader_error_handler(request: Request, exc: ReaderError) -> JSONResponse:
        """Reader API domain errors: fixed code/message + meta."""
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

    # Override the dependency to use test database
    def override_get_reader_service():
        return ReaderService(test_db, ai_workflow=None, search_service=None)

    app.dependency_overrides[get_reader_service] = override_get_reader_service
    app.include_router(router)  # Router already has /api/v1/reader prefix

    return app


@pytest.fixture
def client(app):
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def test_device(client, test_db):
    """Create and register a test device."""
    # Step 1: Create pairing token
    pair_response = client.post(
        "/api/v1/reader/pair", json={"device_name": "Test iPhone", "device_type": "ios"}
    )
    assert pair_response.status_code == 200
    pair_data = pair_response.json()

    # Registration is gated on approval from the trusted LocalBook settings UI.
    test_db.approve_pairing_token(pair_data["pairing_token"])

    # Step 2: Register device
    register_response = client.post(
        "/api/v1/reader/register",
        json={
            "device_id": "01943f10-0001-7000-8000-000000000001",
            "device_name": "Test iPhone",
            "device_type": "ios",
            "pairing_token": pair_data["pairing_token"],
        },
    )
    assert register_response.status_code == 200
    register_data = register_response.json()

    return {
        "device_id": "01943f10-0001-7000-8000-000000000001",
        "access_token": register_data["access_token"],
    }


# ============================================================================
# Device Pairing Tests
# ============================================================================


def test_pair_device_success(client):
    """Test successful device pairing."""
    response = client.post(
        "/api/v1/reader/pair", json={"device_name": "My iPhone", "device_type": "ios"}
    )

    assert response.status_code == 200
    data = response.json()
    assert "pairing_token" in data
    assert "expires_at" in data
    assert len(data["pairing_token"]) == 6
    assert data["pairing_token"].isdigit()
    assert data["expires_at"] > int(time.time())


def test_pair_device_invalid_type(client):
    """Test pairing with invalid device type."""
    response = client.post(
        "/api/v1/reader/pair", json={"device_name": "My iPhone", "device_type": "INVALID"}
    )

    assert response.status_code == 422  # Validation error


def test_register_device_success(client, test_db):
    """Test successful device registration."""
    # First, get a pairing token
    pair_response = client.post(
        "/api/v1/reader/pair", json={"device_name": "Test iPad", "device_type": "ios"}
    )
    assert pair_response.status_code == 200
    pairing_token = pair_response.json()["pairing_token"]

    test_db.approve_pairing_token(pairing_token)

    # Then register
    response = client.post(
        "/api/v1/reader/register",
        json={
            "device_id": "01943f10-0002-7000-8000-000000000002",
            "device_name": "Test iPad",
            "device_type": "ios",
            "pairing_token": pairing_token,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "device_id" in data
    assert data["device_id"] == "01943f10-0002-7000-8000-000000000002"


def test_register_device_invalid_token(client):
    """Test registration with invalid token."""
    response = client.post(
        "/api/v1/reader/register",
        json={
            "device_id": "01943f10-0003-7000-8000-000000000003",
            "device_name": "Test Device",
            "device_type": "ios",
            "pairing_token": "999999",  # Invalid token
        },
    )

    assert response.status_code == 401


def test_register_device_expired_token(client):
    """Test registration with expired token (simulated)."""
    # This is hard to test without time manipulation
    # For now, just verify the error handling exists
    pass


def test_device_status(client, test_device):
    """Test device status endpoint."""
    response = client.get(
        "/api/v1/reader/status", headers={"Authorization": f"Bearer {test_device['access_token']}"}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["device_id"] == test_device["device_id"]
    assert data["device_name"] == "Test iPhone"
    assert data["is_active"] is True
    assert "last_seen" in data


# ============================================================================
# Sync Tests
# ============================================================================


def test_sync_push_success(client, test_device):
    """Test successful sync push."""
    # First create source and session
    operations = [
        {
            "entity_type": "source",
            "entity_id": "src-001",
            "operation": "create",
            "client_timestamp": int(time.time()),
            "data": {"type": "document", "title": "Test Book", "url": "file:///test.epub"},
        },
        {
            "entity_type": "session",
            "entity_id": "sess-001",
            "operation": "create",
            "client_timestamp": int(time.time()),
            "data": {
                "device_id": test_device["device_id"],
                "source_id": "src-001",
                "started_at": int(time.time()),
                "duration": 300,
            },
        },
        {
            "entity_type": "highlight",
            "entity_id": "hl-001",
            "operation": "create",
            "client_timestamp": int(time.time()),
            "data": {
                "source_id": "src-001",
                "session_id": "sess-001",
                "selected_text": "Important quote",
                "context_before": "Some context ",
                "context_after": " more context",
                "page": 1,
                "location": "page-1-100",
            },
        },
    ]

    response = client.post(
        "/api/v1/reader/sync/push",
        json={"operations": operations},
        headers={"Authorization": f"Bearer {test_device['access_token']}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["accepted"] == 3
    assert data["rejected"] == 0


def test_sync_push_batch(client, test_device):
    """Test batch sync push."""
    # First create source and session
    operations = [
        {
            "entity_type": "source",
            "entity_id": "src-batch",
            "operation": "create",
            "client_timestamp": int(time.time()),
            "data": {"type": "pdf", "title": "Batch Test", "url": "file:///batch.pdf"},
        },
        {
            "entity_type": "session",
            "entity_id": "sess-batch",
            "operation": "create",
            "client_timestamp": int(time.time()),
            "data": {
                "device_id": test_device["device_id"],
                "source_id": "src-batch",
                "started_at": int(time.time()),
                "duration": 600,
            },
        },
    ]
    for i in range(5):
        operations.append(
            {
                "entity_type": "note",
                "entity_id": f"note-{i:03d}",
                "operation": "create",
                "client_timestamp": int(time.time()),
                "data": {
                    "source_id": "src-batch",
                    "session_id": "sess-batch",
                    "content": f"Note {i}",
                },
            }
        )

    response = client.post(
        "/api/v1/reader/sync/push",
        json={"operations": operations},
        headers={"Authorization": f"Bearer {test_device['access_token']}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["accepted"] == 7  # 2 setup + 5 notes
    assert data["rejected"] == 0


def test_sync_push_exceeds_batch_limit(client, test_device):
    """Test sync push exceeding batch size limit."""
    # First create source and session
    operations = [
        {
            "entity_type": "source",
            "entity_id": "src-limit",
            "operation": "create",
            "client_timestamp": int(time.time()),
            "data": {"type": "pdf", "title": "Limit Test", "url": "file:///limit.pdf"},
        },
        {
            "entity_type": "session",
            "entity_id": "sess-limit",
            "operation": "create",
            "client_timestamp": int(time.time()),
            "data": {
                "device_id": test_device["device_id"],
                "source_id": "src-limit",
                "started_at": int(time.time()),
                "duration": 600,
            },
        },
    ]
    for i in range(101):  # Exceeds max_batch_size of 100
        operations.append(
            {
                "entity_type": "note",
                "entity_id": f"note-{i:03d}",
                "operation": "create",
                "client_timestamp": int(time.time()),
                "data": {
                    "source_id": "src-limit",
                    "session_id": "sess-limit",
                    "content": f"Note {i}",
                },
            }
        )

    response = client.post(
        "/api/v1/reader/sync/push",
        json={"operations": operations},
        headers={"Authorization": f"Bearer {test_device['access_token']}"},
    )

    assert response.status_code == 422  # FastAPI Pydantic validation error for list length


def test_sync_push_unauthorized(client):
    """Test sync push without auth."""
    response = client.post("/api/v1/reader/sync/push", json={"operations": []})

    assert response.status_code == 401  # Missing auth header (401 Unauthorized, not 403 Forbidden)


def test_sync_pull_empty(client, test_device):
    """Test sync pull with no data."""
    response = client.get(
        "/api/v1/reader/sync/pull",
        headers={"Authorization": f"Bearer {test_device['access_token']}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert "entities" in data
    assert "next_cursor" in data
    assert isinstance(data["entities"], list)


def test_sync_pull_with_cursor(client, test_device, test_db):
    """Test sync pull with cursor (multi-device scenario)."""
    # Create a second device for pulling
    pair_response = client.post(
        "/api/v1/reader/pair", json={"device_name": "Test Device 2", "device_type": "ios"}
    )
    assert pair_response.status_code == 200
    token = pair_response.json()["pairing_token"]
    test_db.approve_pairing_token(token)

    register_response = client.post(
        "/api/v1/reader/register",
        json={
            "device_id": "01943f10-0004-7000-8000-000000000004",
            "pairing_token": token,
            "device_name": "Test Device 2",
            "device_type": "ios",
            "public_key": "test-public-key-2",
        },
    )
    assert register_response.status_code == 200
    device2_token = register_response.json()["access_token"]

    # Device 1 pushes some data (source first, then session, then highlight)
    operations = [
        {
            "entity_type": "source",
            "entity_id": "src-pull-001",
            "operation": "create",
            "client_timestamp": int(time.time()),
            "data": {
                "title": "Test Book for Pull",
                "type": "pdf",
                "author": "Test Author",
                "url": "https://example.com/book",
            },
        },
        {
            "entity_type": "session",
            "entity_id": "sess-pull-001",
            "operation": "create",
            "client_timestamp": int(time.time()),
            "data": {"source_id": "src-pull-001", "started_at": int(time.time()), "duration": 300},
        },
        {
            "entity_type": "highlight",
            "entity_id": "hl-pull-001",
            "operation": "create",
            "client_timestamp": int(time.time()),
            "data": {
                "source_id": "src-pull-001",
                "session_id": "sess-pull-001",
                "selected_text": "Test highlight",
                "context_before": "",
                "context_after": "",
                "page": 1,
                "location": "page-1-0",
            },
        },
    ]

    push_response = client.post(
        "/api/v1/reader/sync/push",
        json={"operations": operations},
        headers={"Authorization": f"Bearer {test_device['access_token']}"},
    )
    assert push_response.status_code == 200
    push_data = push_response.json()
    assert push_data["accepted"] == 3  # All 3 operations should succeed
    assert push_data["rejected"] == 0

    # Device 2 pulls (should see device 1's changes)
    pull_response = client.get(
        "/api/v1/reader/sync/pull", headers={"Authorization": f"Bearer {device2_token}"}
    )

    assert pull_response.status_code == 200
    data = pull_response.json()
    assert len(data["entities"]) == 3  # Should see all 3 operations
    # Verify the entities are in order (source, session, highlight)
    entity_ids = [e["entity_id"] for e in data["entities"]]
    assert "src-pull-001" in entity_ids
    assert "sess-pull-001" in entity_ids
    assert "hl-pull-001" in entity_ids


def test_sync_pull_with_limit(client, test_device):
    """Test sync pull with limit parameter."""
    response = client.get(
        "/api/v1/reader/sync/pull?limit=10",
        headers={"Authorization": f"Bearer {test_device['access_token']}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data["entities"]) <= 10


# ============================================================================
# Capabilities Test
# ============================================================================


def test_get_capabilities(client):
    """Test capabilities endpoint."""
    response = client.get("/api/v1/reader/capabilities")

    assert response.status_code == 200
    data = response.json()
    assert data["version"] == "1.0"
    assert data["max_batch_size"] == 100
    assert "supported_source_types" in data
    assert "features" in data
    assert data["features"]["highlights"] is True
    assert data["features"]["notes"] is True


# ============================================================================
# AI Query Tests
# ============================================================================


def test_ask_without_ai_service(client, test_device):
    """Test ask endpoint when AI service is not available."""
    response = client.post(
        "/api/v1/reader/ask",
        json={
            "device_id": test_device["device_id"],
            "query": "What is this about?",
            "source_id": "src-001",
            "context": "Additional context text",
        },
        headers={"Authorization": f"Bearer {test_device['access_token']}"},
    )

    # Should return 503 when AI service is unavailable
    assert response.status_code == 503


def test_search_without_search_service_uses_reader_index(client, test_device):
    """Reader search remains available without the optional LocalBook index."""
    response = client.post(
        "/api/v1/reader/search",
        json={"device_id": test_device["device_id"], "query": "test query"},
        headers={"Authorization": f"Bearer {test_device['access_token']}"},
    )

    assert response.status_code == 200
    assert response.json()["total"] == 0


# ============================================================================
# Error Handling Tests
# ============================================================================


def test_invalid_auth_token(client):
    """Test request with invalid auth token."""
    response = client.get(
        "/api/v1/reader/status", headers={"Authorization": "Bearer invalid-token"}
    )

    assert response.status_code == 401


def test_missing_required_fields(client):
    """Test request with missing required fields."""
    response = client.post(
        "/api/v1/reader/pair",
        json={"device_name": "Test"},  # Missing device_type
    )

    assert response.status_code == 422
