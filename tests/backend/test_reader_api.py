"""Tests for Reader API endpoints."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path

import pytest
from tests.backend.client import TestClient

from server.api.main import create_app
from server.ai.schemas import ChatResponse
from server.reader.database import ReaderDatabase
from server.reader.errors import ReaderError
from server.reader.schemas import DeviceType, SourceType, SyncOperation
from server.reader.service import generate_jwt_token, generate_uuidv7


@pytest.fixture
def reader_db(tmp_path: Path) -> ReaderDatabase:
    """Create a test reader database."""
    db_path = tmp_path / "test_reader.db"
    return ReaderDatabase(str(db_path))


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    """Create test client with reader database."""
    # Set up a temporary vault root for the reader database
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    localnote_dir = vault_root / ".localnote"
    localnote_dir.mkdir()

    monkeypatch.setenv("LOCALNOTE_VAULT_ROOT", str(vault_root))

    return TestClient(create_app())


def _approve_pairing(client: TestClient, token: str) -> None:
    response = client.post(
        "/api/v1/settings/reader/pairing/approve", json={"pairing_token": token}
    )
    assert response.status_code == 200, response.text


class _FakeAIWorkflow:
    async def chat(self, request) -> ChatResponse:
        return ChatResponse(answer="Test answer", citations=[], model="test-model")


def _enable_fake_ai(client: TestClient) -> None:
    client.app.state.reader_service.ai_workflow = _FakeAIWorkflow()


# ============================================================================
# Device Pairing Tests
# ============================================================================

def test_pair_device_success(client: TestClient):
    """Test successful device pairing token generation."""
    response = client.post(
        "/api/v1/reader/pair",
        json={
            "device_name": "MacBook Pro",
            "device_type": "macos",
        },
    )

    assert response.status_code == 200
    data = response.json()

    assert "pairing_token" in data
    assert len(data["pairing_token"]) == 6
    assert data["pairing_token"].isdigit()
    assert "expires_at" in data
    assert data["expires_at"] > int(time.time())


def test_pair_device_invalid_type(client: TestClient):
    """Test pairing with invalid device type."""
    response = client.post(
        "/api/v1/reader/pair",
        json={
            "device_name": "Test Device",
            "device_type": "invalid",
        },
    )

    assert response.status_code == 422


def test_register_device_success(client: TestClient):
    """Test successful device registration."""
    # First, get a pairing token
    pair_response = client.post(
        "/api/v1/reader/pair",
        json={
            "device_name": "MacBook Pro",
            "device_type": "macos",
        },
    )
    assert pair_response.status_code == 200
    pairing_token = pair_response.json()["pairing_token"]

    # Generate device_id (in real scenario, client generates this)
    device_id = generate_uuidv7()

    # Registration must be blocked until the trusted settings UI approves it.
    unapproved = client.post(
        "/api/v1/reader/register",
        json={
            "device_id": device_id,
            "device_name": "MacBook Pro",
            "device_type": "macos",
            "pairing_token": pairing_token,
        },
    )
    assert unapproved.status_code == 403
    _approve_pairing(client, pairing_token)

    # Register the device
    register_response = client.post(
        "/api/v1/reader/register",
        json={
            "device_id": device_id,
            "device_name": "MacBook Pro",
            "device_type": "macos",
            "pairing_token": pairing_token,
        },
    )

    assert register_response.status_code == 200
    data = register_response.json()

    assert "access_token" in data
    assert data["device_id"] == device_id
    assert data["access_token"].startswith("reader_token_")


def test_register_device_invalid_token(client: TestClient):
    """Test registration with invalid pairing token."""
    device_id = generate_uuidv7()

    response = client.post(
        "/api/v1/reader/register",
        json={
            "device_id": device_id,
            "device_name": "MacBook Pro",
            "device_type": "macos",
            "pairing_token": "999999",  # Invalid token
        },
    )

    assert response.status_code == 401
    error = response.json()["error"]
    assert error["code"] == "invalid_pairing_token"


def test_register_device_expired_token(reader_db: ReaderDatabase):
    """Test registration with an expired pairing token."""
    device_id = generate_uuidv7()
    token, _ = reader_db.create_pairing_token("Test Device", DeviceType.MACOS)

    # Expiration belongs to the pairing token, not to a device row (the device
    # does not exist until registration succeeds).
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    conn = reader_db._get_connection()
    conn.execute(
        "UPDATE pairing_tokens SET expires_at = ? WHERE token_hash = ?",
        (int(time.time()) - 400, token_hash),
    )
    conn.commit()
    conn.close()

    with pytest.raises(ReaderError) as exc_info:
        reader_db.verify_and_register_device(
            device_id,
            "Test Device",
            DeviceType.MACOS,
            token,
        )

    assert "expired" in str(exc_info.value).lower()


def test_device_status_success(client: TestClient):
    """Test getting device status."""
    # First register a device
    pair_response = client.post(
        "/api/v1/reader/pair",
        json={
            "device_name": "iPhone 14",
            "device_type": "ios",
        },
    )
    pairing_token = pair_response.json()["pairing_token"]
    _approve_pairing(client, pairing_token)
    device_id = generate_uuidv7()

    register_response = client.post(
        "/api/v1/reader/register",
        json={
            "device_id": device_id,
            "device_name": "iPhone 14",
            "device_type": "ios",
            "pairing_token": pairing_token,
        },
    )
    access_token = register_response.json()["access_token"]

    # Get device status; identity comes from the Bearer token.
    response = client.get(
        "/api/v1/reader/status",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 200
    data = response.json()

    assert data["device_id"] == device_id
    assert data["device_name"] == "iPhone 14"
    assert data["device_type"] == "ios"
    assert data["is_active"] is True
    assert "created_at" in data
    assert "last_seen" in data


def test_device_status_rejects_unregistered_token(client: TestClient):
    """A syntactically valid-looking token is not enough for authentication."""
    device_id = generate_uuidv7()
    response = client.get(
        "/api/v1/reader/status",
        headers={"Authorization": f"Bearer {generate_jwt_token(device_id)}"},
    )

    assert response.status_code == 401
    error = response.json()["error"]
    assert error["code"] == "unauthorized"


def test_devices_requires_registered_bearer(client: TestClient):
    """Device inventory is not an unauthenticated enumeration endpoint."""
    response = client.get("/api/v1/reader/devices")
    assert response.status_code == 401


# ============================================================================
# Sync Tests
# ============================================================================

def test_push_sync_success(client: TestClient):
    """Test successful sync push."""
    # Register a device first
    pair_response = client.post(
        "/api/v1/reader/pair",
        json={"device_name": "Test Device", "device_type": "macos"},
    )
    pairing_token = pair_response.json()["pairing_token"]
    _approve_pairing(client, pairing_token)
    device_id = generate_uuidv7()

    register_response = client.post(
        "/api/v1/reader/register",
        json={
            "device_id": device_id,
            "device_name": "Test Device",
            "device_type": "macos",
            "pairing_token": pairing_token,
        },
    )
    access_token = register_response.json()["access_token"]

    # Push sync operations; identity comes from the Bearer token.
    source_id = generate_uuidv7()
    response = client.post(
        "/api/v1/reader/sync/push",
        json={
            "operations": [
                {
                    "operation": "create",
                    "entity_type": "source",
                    "entity_id": source_id,
                    "data": {
                        "type": "web",
                        "title": "Test Article",
                        "url": "https://example.com/article",
                        "canonical_url": "https://example.com/article",
                    },
                    "client_timestamp": int(time.time()),
                }
            ],
        },
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 200
    data = response.json()

    assert data["accepted"] == 1
    assert data["rejected"] == 0
    assert len(data["conflicts"]) == 0


def test_push_sync_batch_too_large(client: TestClient):
    """Test sync push with batch exceeding limit."""
    # Register a device
    pair_response = client.post(
        "/api/v1/reader/pair",
        json={"device_name": "Test Device", "device_type": "macos"},
    )
    pairing_token = pair_response.json()["pairing_token"]
    _approve_pairing(client, pairing_token)
    device_id = generate_uuidv7()

    register_response = client.post(
        "/api/v1/reader/register",
        json={
            "device_id": device_id,
            "device_name": "Test Device",
            "device_type": "macos",
            "pairing_token": pairing_token,
        },
    )
    access_token = register_response.json()["access_token"]

    # Create 101 operations (exceeds 100 limit)
    operations = [
        {
            "operation": "create",
            "entity_type": "source",
            "entity_id": generate_uuidv7(),
            "data": {"type": "web", "title": f"Article {i}"},
            "client_timestamp": int(time.time()),
        }
        for i in range(101)
    ]

    response = client.post(
        "/api/v1/reader/sync/push",
        json={
            "operations": operations,
        },
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 422


def test_pull_sync_initial(client: TestClient):
    """Test initial sync pull (no cursor)."""
    # Register a device
    pair_response = client.post(
        "/api/v1/reader/pair",
        json={"device_name": "Device 1", "device_type": "macos"},
    )
    pairing_token = pair_response.json()["pairing_token"]
    _approve_pairing(client, pairing_token)
    device_id = generate_uuidv7()

    register_response = client.post(
        "/api/v1/reader/register",
        json={
            "device_id": device_id,
            "device_name": "Device 1",
            "device_type": "macos",
            "pairing_token": pairing_token,
        },
    )
    access_token = register_response.json()["access_token"]

    # Initial pull (no cursor); identity comes from the Bearer token.
    response = client.get(
        "/api/v1/reader/sync/pull?limit=10",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 200
    data = response.json()

    assert "entities" in data
    assert "next_cursor" in data
    assert "has_more" in data
    assert isinstance(data["entities"], list)
    assert isinstance(data["has_more"], bool)


def test_pull_sync_with_cursor(client: TestClient):
    """Test sync pull with cursor."""
    # Register two devices
    pair_response1 = client.post(
        "/api/v1/reader/pair",
        json={"device_name": "Device 1", "device_type": "macos"},
    )
    _approve_pairing(client, pair_response1.json()["pairing_token"])
    device_id1 = generate_uuidv7()
    register_response1 = client.post(
        "/api/v1/reader/register",
        json={
            "device_id": device_id1,
            "device_name": "Device 1",
            "device_type": "macos",
            "pairing_token": pair_response1.json()["pairing_token"],
        },
    )
    access_token1 = register_response1.json()["access_token"]

    pair_response2 = client.post(
        "/api/v1/reader/pair",
        json={"device_name": "Device 2", "device_type": "ios"},
    )
    _approve_pairing(client, pair_response2.json()["pairing_token"])
    device_id2 = generate_uuidv7()
    register_response2 = client.post(
        "/api/v1/reader/register",
        json={
            "device_id": device_id2,
            "device_name": "Device 2",
            "device_type": "ios",
            "pairing_token": pair_response2.json()["pairing_token"],
        },
    )
    access_token2 = register_response2.json()["access_token"]

    # Device 1 pushes some changes
    source_id = generate_uuidv7()
    client.post(
        "/api/v1/reader/sync/push",
        json={
            "operations": [
                {
                    "operation": "create",
                    "entity_type": "source",
                    "entity_id": source_id,
                    "data": {"type": "web", "title": "Shared Article"},
                    "client_timestamp": int(time.time()),
                }
            ],
        },
        headers={"Authorization": f"Bearer {access_token1}"},
    )

    # Device 2 pulls changes; identity comes from the Bearer token.
    response = client.get(
        "/api/v1/reader/sync/pull",
        headers={"Authorization": f"Bearer {access_token2}"},
    )

    assert response.status_code == 200
    data = response.json()

    # Should see the change from device 1
    assert len(data["entities"]) > 0 or not data["has_more"]


def test_capabilities(client: TestClient):
    """Test server capabilities endpoint."""
    response = client.get("/api/v1/reader/capabilities")

    assert response.status_code == 200
    data = response.json()

    assert data["version"] == "1.0"
    assert data["max_batch_size"] == 100
    assert "supported_source_types" in data
    assert "web" in data["supported_source_types"]
    assert "features" in data
    assert data["features"]["ai_chat"] is True


# ============================================================================
# AI Query Tests
# ============================================================================

def test_ask_ai_new_conversation(client: TestClient):
    """Test AI query starting new conversation."""
    # Register a device
    pair_response = client.post(
        "/api/v1/reader/pair",
        json={"device_name": "Test Device", "device_type": "macos"},
    )
    pairing_token = pair_response.json()["pairing_token"]
    _approve_pairing(client, pairing_token)
    device_id = generate_uuidv7()

    register_response = client.post(
        "/api/v1/reader/register",
        json={
            "device_id": device_id,
            "device_name": "Test Device",
            "device_type": "macos",
            "pairing_token": pairing_token,
        },
    )
    access_token = register_response.json()["access_token"]

    _enable_fake_ai(client)

    # Ask AI without conversation_id; identity comes from the Bearer token.
    response = client.post(
        "/api/v1/reader/ask",
        json={"query": "What is this article about?"},
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 200
    data = response.json()

    assert "conversation_id" in data
    assert "message_id" in data
    assert "answer" in data
    assert len(data["conversation_id"]) > 0
    assert len(data["message_id"]) > 0


def test_ask_ai_existing_conversation(client: TestClient):
    """Test AI query in existing conversation."""
    # Register a device
    pair_response = client.post(
        "/api/v1/reader/pair",
        json={"device_name": "Test Device", "device_type": "macos"},
    )
    pairing_token = pair_response.json()["pairing_token"]
    _approve_pairing(client, pairing_token)
    device_id = generate_uuidv7()

    register_response = client.post(
        "/api/v1/reader/register",
        json={
            "device_id": device_id,
            "device_name": "Test Device",
            "device_type": "macos",
            "pairing_token": pairing_token,
        },
    )
    access_token = register_response.json()["access_token"]

    _enable_fake_ai(client)

    # First message; identity comes from the Bearer token.
    response1 = client.post(
        "/api/v1/reader/ask",
        json={"query": "Hello"},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    conversation_id = response1.json()["conversation_id"]

    # Follow-up message in same conversation
    response2 = client.post(
        "/api/v1/reader/ask",
        json={
            "query": "Tell me more",
            "conversation_id": conversation_id,
        },
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response2.status_code == 200
    data = response2.json()
    assert data["conversation_id"] == conversation_id


def test_search(client: TestClient):
    """Test cross-Reader+LocalBook search."""
    # Register a device
    pair_response = client.post(
        "/api/v1/reader/pair",
        json={"device_name": "Test Device", "device_type": "macos"},
    )
    pairing_token = pair_response.json()["pairing_token"]
    _approve_pairing(client, pairing_token)
    device_id = generate_uuidv7()

    register_response = client.post(
        "/api/v1/reader/register",
        json={
            "device_id": device_id,
            "device_name": "Test Device",
            "device_type": "macos",
            "pairing_token": pairing_token,
        },
    )
    access_token = register_response.json()["access_token"]

    # Search; identity comes from the Bearer token.
    response = client.post(
        "/api/v1/reader/search",
        json={
            "query": "machine learning",
            "limit": 10,
        },
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 200
    data = response.json()

    assert "results" in data
    assert "total" in data
    assert isinstance(data["results"], list)
    assert isinstance(data["total"], int)


# ============================================================================
# Database Layer Tests
# ============================================================================

def test_database_schema_initialization(tmp_path: Path):
    """Test that database schema is properly initialized."""
    db_path = tmp_path / "test.db"
    db = ReaderDatabase(str(db_path))

    # Check that tables exist
    conn = db._get_connection()
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = [row[0] for row in cursor.fetchall()]
    conn.close()

    expected_tables = [
        "devices",
        "reader_conversations",
        "reader_highlights",
        "reader_messages",
        "reader_notes",
        "reader_sessions",
        "reader_sources",
        "reader_sync_outbox",
    ]

    for table in expected_tables:
        assert table in tables


def test_database_foreign_keys_enabled(reader_db: ReaderDatabase):
    """Test that foreign key constraints are enabled."""
    conn = reader_db._get_connection()
    cursor = conn.execute("PRAGMA foreign_keys")
    fk_status = cursor.fetchone()[0]
    conn.close()

    assert fk_status == 1  # Foreign keys should be enabled


def test_create_and_get_device(reader_db: ReaderDatabase):
    """Test creating and retrieving a device."""
    device_id = generate_uuidv7()
    token, _ = reader_db.create_pairing_token("Test Device", DeviceType.MACOS)
    reader_db.approve_pairing_token(token)

    # Verify and register
    reader_db.verify_and_register_device(
        device_id,
        "Test Device",
        DeviceType.MACOS,
        token,
        "test_public_key",
    )

    # Get device
    device = reader_db.get_device(device_id)

    assert device["id"] == device_id
    assert device["device_name"] == "Test Device"
    assert device["device_type"] == "macos"
    assert device["public_key"] == "test_public_key"
