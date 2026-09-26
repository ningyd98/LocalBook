"""Real app HTTP tests without dependency overrides.

This test module uses the actual FastAPI app created by server.api.main.create_app
with real dependencies to catch issues like missing imports that would cause
422/500 errors in production but are hidden when using dependency_overrides.

The key difference from test_api.py and test_reader_api.py is that this test
does NOT register any dependency_overrides, ensuring the real dependency
injection path is exercised.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.api.main import create_app
from server.config import Settings


@pytest.fixture
def real_app(tmp_path: Path):
    """Create a real FastAPI app without any dependency overrides.

    Uses a temporary vault directory to avoid touching the user's real config
    at ~/.config/localnote/.
    """
    # Build isolated settings with temporary vault
    settings = Settings()
    settings.vault.root = tmp_path / "vault"
    settings.vault.root.mkdir(parents=True, exist_ok=True)

    # Create the real app - NO dependency_overrides
    app = create_app(settings)

    return app


@pytest.fixture
def real_client(real_app):
    """Create a test client with the real app."""
    return TestClient(real_app)


def test_openapi_json_without_overrides(real_client):
    """Test that /openapi.json generates without ForwardRef errors.

    This test would fail with:
        PydanticUserError: TypeAdapter[Annotated[ForwardRef('Request'), ...]]
        is not fully defined
    if Request is not imported in router.py.
    """
    response = real_client.get("/openapi.json")
    assert response.status_code == 200, f"OpenAPI failed: {response.text}"
    schema = response.json()
    assert "openapi" in schema
    assert "paths" in schema
    assert "/api/v1/reader/capabilities" in schema["paths"]
    assert "/api/v1/reader/pair" in schema["paths"]


def test_capabilities_endpoint_without_overrides(real_client):
    """Test /capabilities works with real dependencies.

    This would return 422 if get_reader_service has unresolved type annotations.
    """
    response = real_client.get("/api/v1/reader/capabilities")
    assert response.status_code == 200, f"Got {response.status_code}: {response.text}"
    data = response.json()
    assert "version" in data
    assert "max_batch_size" in data
    assert "supported_source_types" in data
    assert "features" in data


def test_pair_endpoint_without_overrides(real_client):
    """Test /pair works with real dependencies and returns 6-digit token."""
    response = real_client.post(
        "/api/v1/reader/pair", json={"device_name": "Test Device", "device_type": "macos"}
    )
    assert response.status_code == 200, f"Got {response.status_code}: {response.text}"
    data = response.json()
    assert "pairing_token" in data
    assert "expires_at" in data
    # Verify it's a 6-digit token
    assert len(data["pairing_token"]) == 6
    assert data["pairing_token"].isdigit()


def test_register_endpoint_without_overrides(real_client):
    """Test /register works with real dependencies and returns access_token."""
    # First get a pairing token
    pair_response = real_client.post(
        "/api/v1/reader/pair", json={"device_name": "Test Device", "device_type": "macos"}
    )
    assert pair_response.status_code == 200
    token = pair_response.json()["pairing_token"]
    approval = real_client.post(
        "/api/v1/settings/reader/pairing/approve", json={"pairing_token": token}
    )
    assert approval.status_code == 200, approval.text

    # Then register with that token (use UUID format for device_id)
    response = real_client.post(
        "/api/v1/reader/register",
        json={
            "device_id": "01943f10-cccc-7000-8000-0000000000c1",
            "device_name": "Test Device",
            "device_type": "macos",
            "pairing_token": token,
        },
    )
    assert response.status_code == 200, f"Got {response.status_code}: {response.text}"
    data = response.json()
    assert "access_token" in data
    assert "device_id" in data
    assert data["device_id"] == "01943f10-cccc-7000-8000-0000000000c1"
    # Tokens are opaque and must not encode the device identity.
    assert data["access_token"].startswith("reader_token_")
    assert data["device_id"] not in data["access_token"]


def test_devices_list_without_overrides(real_client):
    """Test authenticated /devices endpoint with real dependencies."""
    pair_response = real_client.post(
        "/api/v1/reader/pair",
        json={"device_name": "Inventory Device", "device_type": "macos"},
    )
    token = pair_response.json()["pairing_token"]
    approval = real_client.post(
        "/api/v1/settings/reader/pairing/approve", json={"pairing_token": token}
    )
    assert approval.status_code == 200, approval.text
    register_response = real_client.post(
        "/api/v1/reader/register",
        json={
            "device_id": "01943f10-dddd-7000-8000-0000000000d1",
            "device_name": "Inventory Device",
            "device_type": "macos",
            "pairing_token": token,
        },
    )
    access_token = register_response.json()["access_token"]

    response = real_client.get(
        "/api/v1/reader/devices",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert response.status_code == 200, f"Got {response.status_code}: {response.text}"
    data = response.json()
    assert isinstance(data, list)
    assert any(item["device_id"] == "01943f10-dddd-7000-8000-0000000000d1" for item in data)


def test_register_with_uppercase_uuid(real_client):
    """Test that register accepts uppercase UUID (macOS UUID().uuidString format).

    macOS client uses UUID().uuidString which produces uppercase letters with hyphens,
    e.g., "01943F10-AAAA-7000-8000-0000000000A1". The validation must be case-insensitive.
    """
    # Get pairing token
    pair_response = real_client.post(
        "/api/v1/reader/pair", json={"device_name": "macOS Device", "device_type": "macos"}
    )
    assert pair_response.status_code == 200
    token = pair_response.json()["pairing_token"]
    approval = real_client.post(
        "/api/v1/settings/reader/pairing/approve", json={"pairing_token": token}
    )
    assert approval.status_code == 200, approval.text

    # Register with UPPERCASE UUID (real macOS format)
    response = real_client.post(
        "/api/v1/reader/register",
        json={
            "device_id": "01943F10-AAAA-7000-8000-0000000000A1",  # UPPERCASE
            "device_name": "macOS Device",
            "device_type": "macos",
            "pairing_token": token,
        },
    )
    assert response.status_code == 200, f"Uppercase UUID rejected: {response.text}"
    data = response.json()
    assert "access_token" in data
    assert data["device_id"] == "01943F10-AAAA-7000-8000-0000000000A1"


def test_register_with_invalid_device_id(real_client):
    """Test that register rejects non-UUID device_id with clear error.

    This ensures fail-fast validation at register time rather than confusing
    device_not_found errors during sync operations.
    """
    # Get pairing token
    pair_response = real_client.post(
        "/api/v1/reader/pair", json={"device_name": "Test Device", "device_type": "macos"}
    )
    assert pair_response.status_code == 200
    token = pair_response.json()["pairing_token"]
    approval = real_client.post(
        "/api/v1/settings/reader/pairing/approve", json={"pairing_token": token}
    )
    assert approval.status_code == 200, approval.text

    # Try to register with underscore-containing device_id (non-UUID)
    response = real_client.post(
        "/api/v1/reader/register",
        json={
            "device_id": "deva_patha_1789243690",  # Invalid: contains underscores
            "device_name": "Test Device",
            "device_type": "macos",
            "pairing_token": token,
        },
    )
    assert response.status_code == 400, f"Expected 400, got {response.status_code}: {response.text}"
    data = response.json()
    assert "error" in data
    assert data["error"]["code"] == "invalid_device_id"
    assert "UUID" in data["error"]["message"]


def test_authenticated_sync_roundtrip_without_overrides(real_client):
    """Test authenticated sync push/pull roundtrip with real app.

    This test verifies:
    1. The token parsing and authentication path is exercised with real dependencies
    2. Device registration, push, and pull work end-to-end without overrides
    3. Sync roundtrip consistency (device A pushes, device B receives)

    Note: This uses UUID-format device_ids, so it exercises the correct-path token
    parsing. Underscore device_id scenarios are covered by unit tests in
    test_token_parsing.py which directly call get_device_id_from_token().
    """
    # Register device A
    pair_a = real_client.post(
        "/api/v1/reader/pair", json={"device_name": "Device A", "device_type": "macos"}
    )
    assert pair_a.status_code == 200
    token_a = pair_a.json()["pairing_token"]
    approval_a = real_client.post(
        "/api/v1/settings/reader/pairing/approve", json={"pairing_token": token_a}
    )
    assert approval_a.status_code == 200, approval_a.text

    register_a = real_client.post(
        "/api/v1/reader/register",
        json={
            "device_id": "01943f10-aaaa-7000-8000-0000000000a1",  # UUID format
            "device_name": "Device A",
            "device_type": "macos",
            "pairing_token": token_a,
        },
    )
    assert register_a.status_code == 200, f"Register A failed: {register_a.text}"
    access_token_a = register_a.json()["access_token"]

    # Register device B
    pair_b = real_client.post(
        "/api/v1/reader/pair", json={"device_name": "Device B", "device_type": "macos"}
    )
    assert pair_b.status_code == 200
    token_b = pair_b.json()["pairing_token"]
    approval_b = real_client.post(
        "/api/v1/settings/reader/pairing/approve", json={"pairing_token": token_b}
    )
    assert approval_b.status_code == 200, approval_b.text

    register_b = real_client.post(
        "/api/v1/reader/register",
        json={
            "device_id": "01943f10-bbbb-7000-8000-0000000000b1",  # UUID format
            "device_name": "Device B",
            "device_type": "macos",
            "pairing_token": token_b,
        },
    )
    assert register_b.status_code == 200, f"Register B failed: {register_b.text}"
    access_token_b = register_b.json()["access_token"]

    # Device A pushes sync operations
    import time

    push_response = real_client.post(
        "/api/v1/reader/sync/push",
        json={
            "operations": [
                {
                    "operation": "create",
                    "entity_type": "source",
                    "entity_id": "src_001",
                    "data": {"type": "web", "title": "Test Source", "url": "https://example.com"},
                    "client_timestamp": int(time.time()),
                }
            ]
        },
        headers={"Authorization": f"Bearer {access_token_a}"},
    )
    assert push_response.status_code == 200, f"Push failed: {push_response.text}"
    push_data = push_response.json()
    assert push_data["accepted"] == 1
    assert push_data["rejected"] == 0

    # Device B pulls and should receive A's operation
    pull_response = real_client.get(
        "/api/v1/reader/sync/pull", headers={"Authorization": f"Bearer {access_token_b}"}
    )
    assert pull_response.status_code == 200, f"Pull failed: {pull_response.text}"
    pull_data = pull_response.json()

    # Verify B received A's operation (note: response uses "entities" not "operations")
    assert "entities" in pull_data, f"Missing 'entities' key in response: {pull_data}"
    assert len(pull_data["entities"]) >= 1, "Expected at least 1 entity from device A"
    entity = pull_data["entities"][0]
    assert entity["entity_type"] == "source"
    assert entity["entity_id"] == "src_001"
    assert entity["data"]["title"] == "Test Source"
