"""Comprehensive tests for Reader API endpoints."""

from __future__ import annotations

import json
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
def db():
    """Create a temporary test database."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    database = ReaderDatabase(db_path)
    yield database

    Path(db_path).unlink(missing_ok=True)


@pytest.fixture
def app(db):
    """Create test FastAPI app."""
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

    def override_get_reader_service():
        return ReaderService(db, ai_workflow=None, search_service=None)

    app.dependency_overrides[get_reader_service] = override_get_reader_service
    app.include_router(router)

    return app


@pytest.fixture
def client(app):
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def registered_device(client, db):
    """Create a registered device and return device_id and access_token."""
    # Step 1: Pair
    pair_response = client.post(
        "/api/v1/reader/pair",
        json={"device_name": "Test Device", "device_type": "macos"},
    )
    assert pair_response.status_code == 200
    pairing_token = pair_response.json()["pairing_token"]
    db.approve_pairing_token(pairing_token)

    # Step 2: Register
    register_response = client.post(
        "/api/v1/reader/register",
        json={
            "device_id": "01943f10-1001-7000-8000-000000001001",
            "device_name": "Test Device",
            "device_type": "macos",
            "pairing_token": pairing_token,
        },
    )
    assert register_response.status_code == 200
    data = register_response.json()

    return {"device_id": data["device_id"], "access_token": data["access_token"]}


# ============================================================================
# Device Pairing & Registration Tests
# ============================================================================


class TestDevicePairing:
    """Test device pairing and registration flow."""

    def test_pair_device_success(self, client):
        """Test successful pairing token generation."""
        response = client.post(
            "/api/v1/reader/pair",
            json={"device_name": "Test iPhone", "device_type": "ios"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "pairing_token" in data
        assert "expires_at" in data
        assert len(data["pairing_token"]) == 6
        assert data["pairing_token"].isdigit()

    def test_pair_device_invalid_type(self, client):
        """Test pairing with invalid device type."""
        response = client.post(
            "/api/v1/reader/pair",
            json={"device_name": "Test Device", "device_type": "invalid"},
        )

        assert response.status_code == 422

    def test_register_device_success(self, client, db):
        """Registration requires trusted owner approval."""
        # Get pairing token
        pair_response = client.post(
            "/api/v1/reader/pair",
            json={"device_name": "Test Mac", "device_type": "macos"},
        )
        pairing_token = pair_response.json()["pairing_token"]

        payload = {
            "device_id": "01943f10-1002-7000-8000-000000001002",
            "device_name": "Test Mac",
            "device_type": "macos",
            "pairing_token": pairing_token,
        }
        blocked = client.post("/api/v1/reader/register", json=payload)
        assert blocked.status_code == 403

        db.approve_pairing_token(pairing_token)
        response = client.post(
            "/api/v1/reader/register",
            json=payload,
        )

        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "device_id" in data
        assert data["device_id"] == "01943f10-1002-7000-8000-000000001002"

    def test_register_device_invalid_token(self, client):
        """Test registration with invalid pairing token."""
        response = client.post(
            "/api/v1/reader/register",
            json={
                "device_id": "01943f10-1003-7000-8000-000000001003",
                "device_name": "Test Device",
                "device_type": "ios",
                "pairing_token": "999999",
            },
        )

        assert response.status_code == 401
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == "invalid_pairing_token"

    def test_register_device_expired_token(self, client, db):
        """Test registration with expired pairing token."""
        # Create expired token directly in database
        import hashlib

        token = "123456"
        token_hash = hashlib.sha256(token.encode()).hexdigest()

        conn = db._get_connection()
        conn.execute(
            """
            INSERT INTO pairing_tokens (token_hash, device_name, device_type, expires_at)
            VALUES (?, ?, ?, ?)
            """,
            (token_hash, "Test Device", "ios", int(time.time()) - 100),
        )
        conn.commit()
        conn.close()

        # Try to register
        response = client.post(
            "/api/v1/reader/register",
            json={
                "device_id": "01943f10-1004-7000-8000-000000001004",
                "device_name": "Test Device",
                "device_type": "ios",
                "pairing_token": token,
            },
        )

        assert response.status_code == 401
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == "pairing_token_expired"

    def test_register_device_mismatched_info(self, client):
        """Test registration with mismatched device info."""
        # Get pairing token for iOS
        pair_response = client.post(
            "/api/v1/reader/pair",
            json={"device_name": "iPhone", "device_type": "ios"},
        )
        pairing_token = pair_response.json()["pairing_token"]

        # Try to register as Android
        response = client.post(
            "/api/v1/reader/register",
            json={
                "device_id": "01943f10-1005-7000-8000-000000001005",
                "device_name": "iPhone",
                "device_type": "android",
                "pairing_token": pairing_token,
            },
        )

        assert response.status_code == 401
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == "invalid_pairing_token"

    def test_register_device_token_reuse(self, client, db):
        """Test that pairing token cannot be reused."""
        # Get pairing token
        pair_response = client.post(
            "/api/v1/reader/pair",
            json={"device_name": "Test Device", "device_type": "ios"},
        )
        pairing_token = pair_response.json()["pairing_token"]
        db.approve_pairing_token(pairing_token)

        # Register first device
        response1 = client.post(
            "/api/v1/reader/register",
            json={
                "device_id": "01943f10-1006-7000-8000-000000001006",
                "device_name": "Test Device",
                "device_type": "ios",
                "pairing_token": pairing_token,
            },
        )
        assert response1.status_code == 200

        # Try to register second device with same token
        response2 = client.post(
            "/api/v1/reader/register",
            json={
                "device_id": "01943f10-1007-7000-8000-000000001007",
                "device_name": "Test Device",
                "device_type": "ios",
                "pairing_token": pairing_token,
            },
        )
        assert response2.status_code == 401
        data = response2.json()
        assert "error" in data
        assert data["error"]["code"] == "pairing_token_expired"


class TestDeviceStatus:
    """Test device status endpoint."""

    def test_get_status_success(self, client, registered_device):
        """Test successful status retrieval."""
        response = client.get(
            "/api/v1/reader/status",
            headers={"Authorization": f"Bearer {registered_device['access_token']}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["device_id"] == registered_device["device_id"]
        assert data["device_name"] == "Test Device"
        assert data["device_type"] == "macos"
        assert "created_at" in data
        assert "last_seen" in data
        assert data["is_active"] is True

    def test_get_status_device_not_found(self, client):
        """Test status for non-existent device."""
        # Create an invalid token
        response = client.get(
            "/api/v1/reader/status", headers={"Authorization": "Bearer invalid_token"}
        )

        assert response.status_code == 401


# ============================================================================
# Sync Tests
# ============================================================================


class TestSyncPush:
    """Test sync push endpoint."""

    def test_push_operations_success(self, client, registered_device):
        """Test successful push of sync operations."""
        operations = [
            # First create the source
            {
                "entity_type": "source",
                "operation": "create",
                "entity_id": "source-001",
                "data": {
                    "type": "web",
                    "title": "Test Article",
                    "url": "https://example.com",
                },
                "client_timestamp": int(time.time()),
            },
            # Then create a session referencing it
            {
                "entity_type": "session",
                "operation": "create",
                "entity_id": "session-001",
                "data": {
                    "source_id": "source-001",
                    "started_at": int(time.time()),
                },
                "client_timestamp": int(time.time()),
            },
        ]

        response = client.post(
            "/api/v1/reader/sync/push",
            json={"operations": operations},
            headers={"Authorization": f"Bearer {registered_device['access_token']}"},
        )

        print(f"Response: {response.json()}")

        assert response.status_code == 200
        data = response.json()
        assert data["accepted"] == 2
        assert data["rejected"] == 0

    def test_push_operations_unauthorized(self, client):
        """Test push without authentication."""
        response = client.post(
            "/api/v1/reader/sync/push",
            json={"operations": []},
        )

        assert response.status_code == 401

    def test_push_operations_empty(self, client, registered_device):
        """Test push with empty operations list."""
        response = client.post(
            "/api/v1/reader/sync/push",
            json={"operations": []},
            headers={"Authorization": f"Bearer {registered_device['access_token']}"},
        )

        assert response.status_code == 422  # Pydantic validation: min_length=1

    def test_push_operations_too_many(self, client, registered_device):
        """Test push with more than 100 operations."""
        operations = [
            {
                "entity_type": "highlight",
                "operation": "create",
                "entity_id": f"highlight-{i:03d}",
                "data": {"selected_text": f"Highlight {i}"},
                "client_timestamp": int(time.time()),
            }
            for i in range(101)
        ]

        response = client.post(
            "/api/v1/reader/sync/push",
            json={"operations": operations},
            headers={"Authorization": f"Bearer {registered_device['access_token']}"},
        )

        assert response.status_code == 422  # Pydantic validation: max_length=100

    def test_push_operations_batch(self, client, registered_device):
        """Test push with multiple valid operations."""
        # First create a source that the notes can reference
        operations = [
            {
                "entity_type": "source",
                "operation": "create",
                "entity_id": "source-batch-001",
                "data": {
                    "type": "web",
                    "title": "Batch Test Source",
                    "url": "https://example.com/batch",
                },
                "client_timestamp": int(time.time()),
            }
        ]
        # Then create notes referencing that source
        operations.extend(
            [
                {
                    "entity_type": "note",
                    "operation": "create",
                    "entity_id": f"note-{i:03d}",
                    "data": {"source_id": "source-batch-001", "content": f"Note {i}"},
                    "client_timestamp": int(time.time()),
                }
                for i in range(10)
            ]
        )

        response = client.post(
            "/api/v1/reader/sync/push",
            json={"operations": operations},
            headers={"Authorization": f"Bearer {registered_device['access_token']}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["accepted"] == 11  # 1 source + 10 notes
        assert data["rejected"] == 0


class TestSyncPull:
    """Test sync pull endpoint."""

    def test_pull_operations_success(self, client, registered_device, db):
        """Test successful pull of sync operations."""
        # Insert some sync operations from a DIFFERENT device
        device_id = registered_device["device_id"]
        other_device_id = "other-test-device-999"  # Different device
        conn = db._get_connection()

        # Register the other device first (FK constraint requirement)
        conn.execute(
            "INSERT INTO devices (id, device_name, device_type, created_at) VALUES (?, ?, ?, ?)",
            (other_device_id, "Other Test Device", "macos", int(time.time())),
        )
        conn.commit()

        for i in range(5):
            conn.execute(
                """
                INSERT INTO reader_sync_outbox
                (device_id, entity_type, operation, entity_id, data, created_at, sync_state)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    other_device_id,  # Insert from other device
                    "session",
                    "create",
                    f"session-{i:03d}",
                    json.dumps({"title": f"Session {i}"}),
                    int(time.time()) + i,
                    "pending",
                ),
            )
        conn.commit()
        conn.close()

        response = client.get(
            f"/api/v1/reader/sync/pull?device_id={device_id}",
            headers={"Authorization": f"Bearer {registered_device['access_token']}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["entities"]) == 5
        assert "next_cursor" in data
        assert data["has_more"] is False

    def test_pull_operations_with_cursor(self, client, registered_device, db):
        """Test pull with cursor pagination."""
        device_id = registered_device["device_id"]
        other_device_id = "other-test-device-888"  # Different device
        conn = db._get_connection()

        # Register the other device first (FK constraint requirement)
        conn.execute(
            "INSERT INTO devices (id, device_name, device_type, created_at) VALUES (?, ?, ?, ?)",
            (other_device_id, "Other Test Device 888", "macos", int(time.time())),
        )
        conn.commit()

        # Insert 150 operations from DIFFERENT device (default limit is 100)
        for i in range(150):
            conn.execute(
                """
                INSERT INTO reader_sync_outbox
                (device_id, entity_type, operation, entity_id, data, created_at, sync_state)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    other_device_id,  # Insert from other device
                    "highlight",
                    "create",
                    f"highlight-{i:03d}",
                    json.dumps({"text": f"Highlight {i}"}),
                    int(time.time()) + i,
                    "pending",
                ),
            )
        conn.commit()
        conn.close()

        # First pull
        response1 = client.get(
            f"/api/v1/reader/sync/pull?device_id={device_id}",
            headers={"Authorization": f"Bearer {registered_device['access_token']}"},
        )
        assert response1.status_code == 200
        data1 = response1.json()
        assert len(data1["entities"]) == 100
        assert data1["has_more"] is True

        # Second pull with cursor
        response2 = client.get(
            f"/api/v1/reader/sync/pull?device_id={device_id}&cursor={data1['next_cursor']}",
            headers={"Authorization": f"Bearer {registered_device['access_token']}"},
        )
        assert response2.status_code == 200
        data2 = response2.json()
        assert len(data2["entities"]) == 50
        assert data2["has_more"] is False

    def test_pull_operations_empty(self, client, registered_device):
        """Test pull when no operations available."""
        response = client.get(
            f"/api/v1/reader/sync/pull?device_id={registered_device['device_id']}",
            headers={"Authorization": f"Bearer {registered_device['access_token']}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["entities"]) == 0
        assert data["has_more"] is False


class TestCapabilities:
    """Test capabilities endpoint."""

    def test_get_capabilities(self, client):
        """Test server capabilities retrieval."""
        response = client.get("/api/v1/reader/capabilities")

        assert response.status_code == 200
        data = response.json()
        assert "version" in data
        assert data["version"] == "1.0"
        assert "max_batch_size" in data
        assert data["max_batch_size"] == 100
        assert "supported_source_types" in data
        # Check that supported_source_types contains expected values
        source_types = data["supported_source_types"]
        assert "web" in source_types or "WEB" in source_types
        assert "features" in data


# ============================================================================
# AI Query Tests
# ============================================================================


class TestAIQuery:
    """Test AI query endpoint."""

    def test_ask_without_ai(self, client, registered_device):
        """Test ask endpoint when AI workflow is not configured."""
        response = client.post(
            "/api/v1/reader/ask",
            json={
                "device_id": registered_device["device_id"],
                "query": "What is this about?",
            },
            headers={"Authorization": f"Bearer {registered_device['access_token']}"},
        )

        # Should return 503 Service Unavailable
        assert response.status_code == 503

    def test_ask_unauthorized(self, client):
        """Test ask without authentication."""
        response = client.post(
            "/api/v1/reader/ask",
            json={"device_id": "01943f10-9999-7000-8000-000000009999", "query": "Test query"},
        )

        assert response.status_code == 401


class TestSearch:
    """Test search endpoint."""

    def test_search_reader_index(self, client, registered_device):
        source_id = "01943f10-2001-7000-8000-000000001001"
        push = client.post(
            "/api/v1/reader/sync/push",
            json={
                "device_id": registered_device["device_id"],
                "operations": [
                    {
                        "operation": "create",
                        "entity_type": "source",
                        "entity_id": source_id,
                        "data": {
                            "source_type": "web",
                            "title": "Distributed Systems Notes",
                            "url": "https://example.com/systems",
                        },
                        "client_timestamp": int(time.time()),
                    },
                    {
                        "operation": "create",
                        "entity_type": "highlight",
                        "entity_id": "01943f10-2002-7000-8000-000000001002",
                        "data": {
                            "source_id": source_id,
                            "selected_text": "vector clocks and causal order",
                        },
                        "client_timestamp": int(time.time()),
                    },
                ],
            },
            headers={"Authorization": f"Bearer {registered_device['access_token']}"},
        )
        assert push.status_code == 200

        response = client.post(
            "/api/v1/reader/search",
            json={"device_id": registered_device["device_id"], "query": "causal", "limit": 5},
            headers={"Authorization": f"Bearer {registered_device['access_token']}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 1
        assert any(item["entity_type"] == "highlight" for item in data["results"])

    def test_search_without_service_uses_reader_index(self, client, registered_device):
        """Reader search works without the optional LocalBook index."""
        response = client.post(
            "/api/v1/reader/search",
            json={
                "device_id": registered_device["device_id"],
                "query": "test query",
            },
            headers={"Authorization": f"Bearer {registered_device['access_token']}"},
        )

        assert response.status_code == 200
        assert response.json()["total"] == 0

    def test_search_unauthorized(self, client):
        """Test search without authentication."""
        response = client.post(
            "/api/v1/reader/search",
            json={"device_id": "01943f10-9998-7000-8000-000000009998", "query": "test"},
        )

        assert response.status_code == 401
