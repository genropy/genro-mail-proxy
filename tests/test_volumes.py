"""Tests for volume management functionality."""

import pytest
from async_mail_service.persistence import Persistence


@pytest.mark.asyncio
async def test_volume_crud(tmp_path):
    """Test volume create, read, list, delete operations."""
    db = tmp_path / "volumes.db"
    p = Persistence(str(db))
    await p.init_db()

    # Create volumes
    volumes = [
        {"name": "s3-uploads", "backend": "s3", "config": {"bucket": "my-bucket"}, "account_id": None},
        {"name": "tenant1-storage", "backend": "s3", "config": {"bucket": "tenant1"}, "account_id": "tenant1"},
    ]
    await p.add_volumes(volumes)

    # List all volumes
    all_volumes = await p.list_volumes(account_id=None)
    assert len(all_volumes) == 2
    assert any(v["name"] == "s3-uploads" for v in all_volumes)
    assert any(v["name"] == "tenant1-storage" for v in all_volumes)

    # Get specific volume
    vol = await p.get_volume("s3-uploads", account_id=None)
    assert vol is not None
    assert vol["name"] == "s3-uploads"
    assert vol["backend"] == "s3"
    assert vol["config"]["bucket"] == "my-bucket"
    assert vol["account_id"] is None

    # Delete volume
    deleted = await p.delete_volume("s3-uploads", account_id=None)
    assert deleted is True

    # Verify deletion
    vol = await p.get_volume("s3-uploads", account_id=None)
    assert vol is None

    # List after deletion
    remaining = await p.list_volumes(account_id=None)
    assert len(remaining) == 1
    assert remaining[0]["name"] == "tenant1-storage"


@pytest.mark.asyncio
async def test_volume_validation_shared(tmp_path):
    """Test that shared volumes (account_id=NULL) are accessible by all tenants."""
    db = tmp_path / "shared.db"
    p = Persistence(str(db))
    await p.init_db()

    # Create shared volume
    await p.add_volumes([
        {"name": "shared-cdn", "backend": "http", "config": {"base_url": "https://cdn.example.com"}, "account_id": None}
    ])

    # Test access from different accounts
    validation_tenant1 = await p.validate_storage_paths(["shared-cdn:images/logo.png"], "tenant1")
    assert validation_tenant1["shared-cdn:images/logo.png"] is True

    validation_tenant2 = await p.validate_storage_paths(["shared-cdn:images/logo.png"], "tenant2")
    assert validation_tenant2["shared-cdn:images/logo.png"] is True

    # Even with None account_id (no tenant context)
    validation_none = await p.validate_storage_paths(["shared-cdn:images/logo.png"], None)
    assert validation_none["shared-cdn:images/logo.png"] is True


@pytest.mark.asyncio
async def test_volume_validation_tenant_specific(tmp_path):
    """Test that tenant-specific volumes enforce isolation."""
    db = tmp_path / "tenant.db"
    p = Persistence(str(db))
    await p.init_db()

    # Create tenant-specific volumes
    await p.add_volumes([
        {"name": "tenant1-files", "backend": "s3", "config": {"bucket": "tenant1-bucket"}, "account_id": "tenant1"},
        {"name": "tenant2-files", "backend": "s3", "config": {"bucket": "tenant2-bucket"}, "account_id": "tenant2"},
    ])

    # Tenant1 can access its own volume
    validation_tenant1_own = await p.validate_storage_paths(["tenant1-files:doc.pdf"], "tenant1")
    assert validation_tenant1_own["tenant1-files:doc.pdf"] is True

    # Tenant1 CANNOT access tenant2's volume
    validation_tenant1_other = await p.validate_storage_paths(["tenant2-files:doc.pdf"], "tenant1")
    assert validation_tenant1_other["tenant2-files:doc.pdf"] is False

    # Tenant2 can access its own volume
    validation_tenant2_own = await p.validate_storage_paths(["tenant2-files:doc.pdf"], "tenant2")
    assert validation_tenant2_own["tenant2-files:doc.pdf"] is True

    # Tenant2 CANNOT access tenant1's volume
    validation_tenant2_other = await p.validate_storage_paths(["tenant1-files:doc.pdf"], "tenant2")
    assert validation_tenant2_other["tenant1-files:doc.pdf"] is False


@pytest.mark.asyncio
async def test_volume_validation_nonexistent(tmp_path):
    """Test that nonexistent volumes are rejected."""
    db = tmp_path / "nonexistent.db"
    p = Persistence(str(db))
    await p.init_db()

    # No volumes configured
    validation = await p.validate_storage_paths(["unknown-volume:file.txt"], "tenant1")
    assert validation["unknown-volume:file.txt"] is False

    # Add one volume
    await p.add_volumes([
        {"name": "existing", "backend": "local", "config": {"path": "/tmp"}, "account_id": None}
    ])

    # Existing volume works
    validation_existing = await p.validate_storage_paths(["existing:file.txt"], "tenant1")
    assert validation_existing["existing:file.txt"] is True

    # Nonexistent still fails
    validation_missing = await p.validate_storage_paths(["missing:file.txt"], "tenant1")
    assert validation_missing["missing:file.txt"] is False


@pytest.mark.asyncio
async def test_volume_validation_base64_always_allowed(tmp_path):
    """Test that base64 special volume is always valid."""
    db = tmp_path / "base64.db"
    p = Persistence(str(db))
    await p.init_db()

    # No volumes configured, but base64 should work
    validation = await p.validate_storage_paths(["base64:SGVsbG8gV29ybGQ="], "tenant1")
    assert validation["base64:SGVsbG8gV29ybGQ="] is True

    # base64 works for any tenant
    validation_tenant2 = await p.validate_storage_paths(["base64:dGVzdA=="], "tenant2")
    assert validation_tenant2["base64:dGVzdA=="] is True

    # base64 works with no tenant context
    validation_none = await p.validate_storage_paths(["base64:YWJjZA=="], None)
    assert validation_none["base64:YWJjZA=="] is True


@pytest.mark.asyncio
async def test_volume_validation_path_formats(tmp_path):
    """Test validation of various storage path formats."""
    db = tmp_path / "format.db"
    p = Persistence(str(db))
    await p.init_db()

    # Relative paths without colon are now valid (filesystem paths)
    validation_relative = await p.validate_storage_paths(["relativepath"], "tenant1")
    assert validation_relative["relativepath"] is True

    # Absolute paths are valid (filesystem paths)
    validation_absolute = await p.validate_storage_paths(["/absolute/path"], "tenant1")
    assert validation_absolute["/absolute/path"] is True

    # HTTP paths are valid
    validation_http = await p.validate_storage_paths(["@doc_id=123"], "tenant1")
    assert validation_http["@doc_id=123"] is True

    # Empty path is invalid
    validation_empty = await p.validate_storage_paths([""], "tenant1")
    assert validation_empty[""] is False

    # Unconfigured volume is invalid
    validation_unconfigured = await p.validate_storage_paths(["unconfigured:file.txt"], "tenant1")
    assert validation_unconfigured["unconfigured:file.txt"] is False


@pytest.mark.asyncio
async def test_message_rejection_invalid_volume(tmp_path):
    """Test that messages with invalid volumes are rejected during submission."""
    from async_mail_service.core import AsyncMailCore

    db = tmp_path / "message_validation.db"
    service = AsyncMailCore(db_path=str(db), test_mode=True)
    await service.init()

    # Configure SMTP account
    await service.handle_command("addAccount", {
        "id": "test-account",
        "host": "smtp.example.com",
        "port": 587,
        "user": "test@example.com",
        "password": "password"
    })

    # Configure one valid volume
    await service.persistence.add_volumes([
        {"name": "valid-storage", "backend": "s3", "config": {"bucket": "test"}, "account_id": None}
    ])

    # Message with valid volume should be accepted
    payload_valid = {
        "messages": [
            {
                "id": "msg1",
                "account_id": "test-account",
                "from": "sender@example.com",
                "to": "recipient@example.com",
                "subject": "Test",
                "body": "Test body",
                "attachments": [
                    {"filename": "doc.pdf", "storage_path": "valid-storage:documents/doc.pdf"}
                ]
            }
        ]
    }
    result_valid = await service.handle_command("addMessages", payload_valid)
    assert result_valid["ok"] is True
    assert result_valid["queued"] == 1
    assert len(result_valid.get("rejected", [])) == 0

    # Message with invalid volume should be rejected
    payload_invalid = {
        "messages": [
            {
                "id": "msg2",
                "account_id": "test-account",
                "from": "sender@example.com",
                "to": "recipient@example.com",
                "subject": "Test",
                "body": "Test body",
                "attachments": [
                    {"filename": "doc.pdf", "storage_path": "invalid-storage:documents/doc.pdf"}
                ]
            }
        ]
    }
    result_invalid = await service.handle_command("addMessages", payload_invalid)
    assert result_invalid["ok"] is False
    assert result_invalid.get("queued", 0) == 0
    rejected = result_invalid.get("rejected", [])
    assert len(rejected) == 1
    assert "invalid-storage" in rejected[0]["reason"].lower()


@pytest.mark.asyncio
async def test_volume_api_endpoints(tmp_path):
    """Test volume API endpoints (POST/GET/DELETE)."""
    from fastapi.testclient import TestClient
    from async_mail_service.core import AsyncMailCore
    from async_mail_service.api import create_app

    db = tmp_path / "api_volumes.db"
    service = AsyncMailCore(db_path=str(db), test_mode=True)
    await service.init()

    app = create_app(service, api_token=None)
    client = TestClient(app)

    # POST /volume - Create volume
    response_create = client.post("/volume", json={
        "name": "api-test-volume",
        "backend": "s3",
        "config": {"bucket": "api-test-bucket"},
        "account_id": None
    })
    assert response_create.status_code == 200
    assert response_create.json()["ok"] is True

    # GET /volumes - List volumes
    response_list = client.get("/volumes")
    assert response_list.status_code == 200
    volumes_data = response_list.json()
    assert volumes_data["ok"] is True
    volumes = volumes_data["volumes"]
    assert len(volumes) >= 1
    assert any(v["name"] == "api-test-volume" for v in volumes)

    # GET /volume/{name} - Get specific volume
    response_get = client.get("/volume/api-test-volume")
    assert response_get.status_code == 200
    volume_data = response_get.json()
    assert volume_data["name"] == "api-test-volume"
    assert volume_data["backend"] == "s3"

    # DELETE /volume/{name} - Delete volume
    response_delete = client.delete("/volume/api-test-volume")
    assert response_delete.status_code == 200
    assert response_delete.json()["ok"] is True

    # Verify deletion - GET should return 404
    response_get_after = client.get("/volume/api-test-volume")
    assert response_get_after.status_code == 404


@pytest.mark.asyncio
async def test_volume_update_replaces_existing(tmp_path):
    """Test that adding a volume with same name updates it."""
    db = tmp_path / "update.db"
    p = Persistence(str(db))
    await p.init_db()

    # Create initial volume
    await p.add_volumes([
        {"name": "updatable", "backend": "s3", "config": {"bucket": "old-bucket"}, "account_id": None}
    ])

    # Verify initial state
    vol = await p.get_volume("updatable")
    assert vol["config"]["bucket"] == "old-bucket"

    # Update volume (same name, different config)
    await p.add_volumes([
        {"name": "updatable", "backend": "s3", "config": {"bucket": "new-bucket"}, "account_id": None}
    ])

    # Verify update
    vol_updated = await p.get_volume("updatable")
    assert vol_updated["config"]["bucket"] == "new-bucket"

    # Should still be only one volume
    all_vols = await p.list_volumes()
    assert len(all_vols) == 1
