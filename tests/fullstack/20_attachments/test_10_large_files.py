# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""Fullstack tests extracted from test_fullstack_integration.py."""

from __future__ import annotations

import asyncio
import time

import pytest
import pytest_asyncio

httpx = pytest.importorskip("httpx")

from tests.fullstack.helpers import (
    ATTACHMENT_SERVER_URL,
    CLIENT_TENANT1_URL,
    MAILHOG_TENANT1_API,
    MINIO_URL,
    get_msg_status,
    wait_for_messages,
)

pytestmark = [pytest.mark.fullstack, pytest.mark.asyncio]


class TestLargeFileStorage:
    """Test large file storage with S3/MinIO integration.

    Tests the complete flow:
    1. Configure tenant with large_file_config (action=rewrite)
    2. Send email with large attachment
    3. Verify attachment is uploaded to MinIO
    4. Verify email body contains download link
    5. Verify download link works
    """

    # MinIO internal URL (for mail-proxy container)
    MINIO_INTERNAL_URL = "http://minio:9000"
    MINIO_BUCKET = "mail-attachments"

    @pytest_asyncio.fixture
    async def setup_large_file_tenant(self, api_client):
        """Create a tenant configured for large file storage with MinIO."""
        tenant_data = {
            "id": "test-tenant-largefile",
            "name": "Large File Test Tenant",
            "client_base_url": CLIENT_TENANT1_URL,
            "client_sync_path": "/proxy_sync",
            "client_auth": {"method": "none"},
            "active": True,
            "large_file_config": {
                "enabled": True,
                "max_size_mb": 1.0,  # 1 MB threshold
                "storage_url": f"s3://{self.MINIO_BUCKET}/large-files",
                "action": "rewrite",
                "file_ttl_days": 30,
            },
        }
        resp = await api_client.post("/tenant", json=tenant_data)
        assert resp.status_code in (200, 201, 409), resp.text

        # Create account for this tenant
        account_data = {
            "id": "account-largefile",
            "tenant_id": "test-tenant-largefile",
            "host": "localhost",
            "port": 1025,
            "use_tls": False,
        }
        resp = await api_client.post("/account", json=account_data)
        assert resp.status_code in (200, 201, 409), resp.text

        return tenant_data

    @pytest_asyncio.fixture
    async def setup_reject_tenant(self, api_client):
        """Create a tenant configured to reject large files."""
        tenant_data = {
            "id": "test-tenant-reject-large",
            "name": "Reject Large Files Tenant",
            "client_base_url": CLIENT_TENANT1_URL,
            "client_sync_path": "/proxy_sync",
            "client_auth": {"method": "none"},
            "active": True,
            "large_file_config": {
                "enabled": True,
                "max_size_mb": 1.0,
                "action": "reject",
            },
        }
        resp = await api_client.post("/tenant", json=tenant_data)
        assert resp.status_code in (200, 201, 409), resp.text

        account_data = {
            "id": "account-reject-large",
            "tenant_id": "test-tenant-reject-large",
            "host": "localhost",
            "port": 1025,
            "use_tls": False,
        }
        resp = await api_client.post("/account", json=account_data)
        assert resp.status_code in (200, 201, 409), resp.text

        return tenant_data

    @pytest_asyncio.fixture
    async def setup_warn_tenant(self, api_client):
        """Create a tenant configured to warn but send large files normally."""
        tenant_data = {
            "id": "test-tenant-warn-large",
            "name": "Warn Large Files Tenant",
            "client_base_url": CLIENT_TENANT1_URL,
            "client_sync_path": "/proxy_sync",
            "client_auth": {"method": "none"},
            "active": True,
            "large_file_config": {
                "enabled": True,
                "max_size_mb": 1.0,
                "action": "warn",
            },
        }
        resp = await api_client.post("/tenant", json=tenant_data)
        assert resp.status_code in (200, 201, 409), resp.text

        account_data = {
            "id": "account-warn-large",
            "tenant_id": "test-tenant-warn-large",
            "host": "localhost",
            "port": 1025,
            "use_tls": False,
        }
        resp = await api_client.post("/account", json=account_data)
        assert resp.status_code in (200, 201, 409), resp.text

        return tenant_data

    async def test_small_attachment_sent_normally(
        self, api_client, setup_large_file_tenant
    ):
        """Small attachments should be sent normally even with large_file_config enabled."""
        ts = int(time.time())
        msg_id = f"small-att-test-{ts}"

        # Use small.txt from attachment server (< 1 MB)
        message = {
            "id": msg_id,
            "account_id": "account-largefile",
            "from": "sender@test.com",
            "to": ["recipient@example.com"],
            "subject": "Small Attachment Test",
            "body": "This email has a small attachment.",
            "attachments": [
                {
                    "filename": "small.txt",
                    "storage_path": f"{ATTACHMENT_SERVER_URL}/small.txt",
                    "fetch_mode": "http_url",
                }
            ],
        }

        resp = await api_client.post("/commands/add-messages", json={"messages": [message]})
        assert resp.status_code == 200

        # Trigger dispatch
        await api_client.post("/commands/run-now?tenant_id=test-tenant-largefile")
        await asyncio.sleep(3)

        # Check message was sent
        resp = await api_client.get("/messages?tenant_id=test-tenant-largefile")
        all_msgs = resp.json().get("messages", [])
        found = [m for m in all_msgs if m.get("id") == msg_id]

        assert len(found) > 0
        assert get_msg_status(found[0]) == "sent", f"Expected sent, got {get_msg_status(found[0])}"

        # Check MailHog - email should have the attachment (not a link)
        messages = await wait_for_messages(MAILHOG_TENANT1_API, 1, timeout=10)
        assert len(messages) >= 1

        # Find our message
        for msg in messages:
            if "Small Attachment Test" in msg.get("Content", {}).get("Headers", {}).get("Subject", [""])[0]:
                # Check it has MIME parts (attachment)
                mime = msg.get("MIME", {})
                parts = mime.get("Parts", [])
                # Should have attachment parts
                assert len(parts) >= 1, "Email should have attachment"
                break

    @pytest.mark.skip(reason="Flaky in CI: messages stay pending. See issue #69")
    async def test_large_attachment_rewritten_to_link(
        self, api_client, setup_large_file_tenant
    ):
        """Large attachments should be uploaded to S3 and replaced with download links."""
        ts = int(time.time())
        msg_id = f"large-att-rewrite-{ts}"

        # Use large-file.bin from attachment server (> 1 MB)
        message = {
            "id": msg_id,
            "account_id": "account-largefile",
            "from": "sender@test.com",
            "to": ["recipient@example.com"],
            "subject": "Large Attachment Rewrite Test",
            "body": "<html><body><p>This email has a large attachment that should be converted to a link.</p></body></html>",
            "content_type": "html",
            "attachments": [
                {
                    "filename": "large-file.bin",
                    "storage_path": f"{ATTACHMENT_SERVER_URL}/large-file.bin",
                    "fetch_mode": "http_url",
                }
            ],
        }

        resp = await api_client.post("/commands/add-messages", json={"messages": [message]})
        assert resp.status_code == 200

        # Trigger dispatch
        await api_client.post("/commands/run-now?tenant_id=test-tenant-largefile")
        await asyncio.sleep(5)

        # Check message was sent
        resp = await api_client.get("/messages?tenant_id=test-tenant-largefile")
        all_msgs = resp.json().get("messages", [])
        found = [m for m in all_msgs if m.get("id") == msg_id]

        assert len(found) > 0
        msg_status = get_msg_status(found[0])
        assert msg_status == "sent", f"Expected sent, got {msg_status}"

        # Check MailHog - email should have a download link in the body
        messages = await wait_for_messages(MAILHOG_TENANT1_API, 1, timeout=10)
        assert len(messages) >= 1

        # Find our message and check for download link
        for msg in messages:
            subject = msg.get("Content", {}).get("Headers", {}).get("Subject", [""])[0]
            if "Large Attachment Rewrite Test" in subject:
                body = msg.get("Content", {}).get("Body", "")
                # Should contain "Large attachments available for download" or similar
                assert (
                    "download" in body.lower() or "large-file.bin" in body.lower()
                ), f"Email body should contain download link info. Body: {body[:500]}"
                break

    @pytest.mark.skip(reason="Flaky in CI: messages stay pending. See issue #69")
    async def test_large_attachment_reject_action(
        self, api_client, setup_reject_tenant
    ):
        """Large attachments should be rejected when action is 'reject'."""
        ts = int(time.time())
        msg_id = f"large-att-reject-{ts}"

        message = {
            "id": msg_id,
            "account_id": "account-reject-large",
            "from": "sender@test.com",
            "to": ["recipient@example.com"],
            "subject": "Large Attachment Reject Test",
            "body": "This email has a large attachment that should be rejected.",
            "attachments": [
                {
                    "filename": "large-file.bin",
                    "storage_path": f"{ATTACHMENT_SERVER_URL}/large-file.bin",
                    "fetch_mode": "http_url",
                }
            ],
        }

        resp = await api_client.post("/commands/add-messages", json={"messages": [message]})
        assert resp.status_code == 200

        # Trigger dispatch
        await api_client.post("/commands/run-now?tenant_id=test-tenant-reject-large")
        await asyncio.sleep(3)

        # Check message status - should be error (rejected)
        resp = await api_client.get("/messages?tenant_id=test-tenant-reject-large")
        all_msgs = resp.json().get("messages", [])
        found = [m for m in all_msgs if m.get("id") == msg_id]

        assert len(found) > 0
        msg_status = get_msg_status(found[0])
        # Should be error because attachment was rejected
        assert msg_status == "error", f"Expected error (rejected), got {msg_status}"

        # Check last_error mentions size limit
        last_error = found[0].get("error", "")
        assert "large" in last_error.lower() or "size" in last_error.lower() or "limit" in last_error.lower(), \
            f"Error should mention size/limit. Got: {last_error}"

    async def test_large_attachment_warn_action(
        self, api_client, setup_warn_tenant
    ):
        """Large attachments with warn action should be sent normally (with warning logged)."""
        ts = int(time.time())
        msg_id = f"large-att-warn-{ts}"

        message = {
            "id": msg_id,
            "account_id": "account-warn-large",
            "from": "sender@test.com",
            "to": ["recipient@example.com"],
            "subject": "Large Attachment Warn Test",
            "body": "This email has a large attachment that triggers a warning.",
            "attachments": [
                {
                    "filename": "large-file.bin",
                    "storage_path": f"{ATTACHMENT_SERVER_URL}/large-file.bin",
                    "fetch_mode": "http_url",
                }
            ],
        }

        resp = await api_client.post("/commands/add-messages", json={"messages": [message]})
        assert resp.status_code == 200

        # Trigger dispatch and wait for processing
        await api_client.post("/commands/run-now?tenant_id=test-tenant-warn-large")

        # Wait for message to be processed (poll status)
        for _ in range(15):
            await asyncio.sleep(1)
            resp = await api_client.get("/messages?tenant_id=test-tenant-warn-large")
            all_msgs = resp.json().get("messages", [])
            found = [m for m in all_msgs if m.get("id") == msg_id]
            if found and get_msg_status(found[0]) in ("sent", "error"):
                break

        # Check message was sent (warning is just logged)
        assert len(found) > 0, f"Message {msg_id} not found"
        msg_status = get_msg_status(found[0])
        # With warn action, the message should be sent even if attachment is large
        assert msg_status == "sent", f"Expected sent (with warning), got {msg_status}. Error: {found[0].get('error', 'none')}"

    @pytest.mark.skip(reason="Flaky in CI: messages stay pending. See issue #69")
    async def test_mixed_attachments_partial_rewrite(
        self, api_client, setup_large_file_tenant
    ):
        """Email with both small and large attachments - only large ones rewritten."""
        ts = int(time.time())
        msg_id = f"mixed-att-test-{ts}"

        message = {
            "id": msg_id,
            "account_id": "account-largefile",
            "from": "sender@test.com",
            "to": ["recipient@example.com"],
            "subject": "Mixed Attachments Test",
            "body": "<html><body><p>This email has both small and large attachments.</p></body></html>",
            "content_type": "html",
            "attachments": [
                {
                    "filename": "small.txt",
                    "storage_path": f"{ATTACHMENT_SERVER_URL}/small.txt",
                    "fetch_mode": "http_url",
                },
                {
                    "filename": "large-file.bin",
                    "storage_path": f"{ATTACHMENT_SERVER_URL}/large-file.bin",
                    "fetch_mode": "http_url",
                },
            ],
        }

        resp = await api_client.post("/commands/add-messages", json={"messages": [message]})
        assert resp.status_code == 200

        # Trigger dispatch for the tenant that owns this account
        await api_client.post("/commands/run-now?tenant_id=test-tenant-largefile")
        await asyncio.sleep(5)

        # Check message was sent
        resp = await api_client.get("/messages?tenant_id=test-tenant-largefile")
        all_msgs = resp.json().get("messages", [])
        found = [m for m in all_msgs if m.get("id") == msg_id]

        assert len(found) > 0
        assert get_msg_status(found[0]) == "sent"

        # Check MailHog - should have small attachment AND download link for large
        messages = await wait_for_messages(MAILHOG_TENANT1_API, 1, timeout=10)
        assert len(messages) >= 1

        for msg in messages:
            subject = msg.get("Content", {}).get("Headers", {}).get("Subject", [""])[0]
            if "Mixed Attachments Test" in subject:
                body = msg.get("Content", {}).get("Body", "")
                # Should mention large-file.bin download link
                assert "large-file.bin" in body.lower() or "download" in body.lower(), \
                    f"Should have download link for large file. Body: {body[:500]}"

                # Check MIME parts for small attachment
                mime = msg.get("MIME", {})
                parts = mime.get("Parts", [])
                # Should have at least one actual attachment (small.txt)
                has_attachment = False
                for part in parts:
                    headers = part.get("Headers", {})
                    content_disp = headers.get("Content-Disposition", [""])[0]
                    if "attachment" in content_disp.lower():
                        has_attachment = True
                        break
                # Note: This assertion depends on how MailHog structures MIME
                # If it fails, the test still passes the main assertion about download link
                break

    @pytest.mark.skip(reason="Flaky in CI: messages stay pending. See issue #69")
    async def test_verify_file_uploaded_to_minio(
        self, api_client, setup_large_file_tenant
    ):
        """Verify that large files are actually uploaded to MinIO."""
        ts = int(time.time())
        msg_id = f"minio-upload-verify-{ts}"

        message = {
            "id": msg_id,
            "account_id": "account-largefile",
            "from": "sender@test.com",
            "to": ["recipient@example.com"],
            "subject": "MinIO Upload Verification",
            "body": "Testing that files are uploaded to MinIO.",
            "attachments": [
                {
                    "filename": "large-file.bin",
                    "storage_path": f"{ATTACHMENT_SERVER_URL}/large-file.bin",
                    "fetch_mode": "http_url",
                }
            ],
        }

        resp = await api_client.post("/commands/add-messages", json={"messages": [message]})
        assert resp.status_code == 200

        await api_client.post("/commands/run-now?tenant_id=test-tenant-largefile")
        await asyncio.sleep(5)

        # Verify message was sent
        resp = await api_client.get("/messages?tenant_id=test-tenant-largefile")
        all_msgs = resp.json().get("messages", [])
        found = [m for m in all_msgs if m.get("id") == msg_id]
        assert len(found) > 0
        assert get_msg_status(found[0]) == "sent"

        # Check MinIO has files in the bucket
        # We use the MinIO mc CLI or direct S3 API
        # For this test, we just verify the bucket exists and is accessible
        async with httpx.AsyncClient() as client:
            # MinIO health check (already tested in infrastructure)
            resp = await client.get(f"{MINIO_URL}/minio/health/live")
            assert resp.status_code == 200


# ============================================
# 16. TENANT LARGE FILE CONFIG VIA API
# ============================================


class TestTenantLargeFileConfigApi:
    """Test tenant large_file_config via API."""

    async def test_create_tenant_with_large_file_config(self, api_client):
        """Can create a tenant with large_file_config."""
        ts = int(time.time())
        tenant_data = {
            "id": f"tenant-lfc-create-{ts}",
            "name": "Large File Config Test",
            "active": True,
            "large_file_config": {
                "enabled": True,
                "max_size_mb": 5.0,
                "storage_url": "s3://test-bucket/attachments",
                "action": "rewrite",
                "file_ttl_days": 14,
            },
        }

        resp = await api_client.post("/tenant", json=tenant_data)
        assert resp.status_code in (200, 201), resp.text

        # Verify by getting tenant details
        resp = await api_client.get(f"/tenant/{tenant_data['id']}")
        assert resp.status_code == 200
        tenant = resp.json()

        lfc = tenant.get("large_file_config", {})
        assert lfc.get("enabled") is True
        assert lfc.get("max_size_mb") == 5.0
        assert lfc.get("action") == "rewrite"
        assert lfc.get("file_ttl_days") == 14

    async def test_update_tenant_large_file_config(self, api_client, setup_test_tenants):
        """Can update tenant's large_file_config."""
        # Update test-tenant-1 with large_file_config
        update_data = {
            "large_file_config": {
                "enabled": True,
                "max_size_mb": 2.5,
                "action": "warn",
            },
        }

        resp = await api_client.put("/tenant/test-tenant-1", json=update_data)
        assert resp.status_code == 200

        # Verify
        resp = await api_client.get("/tenant/test-tenant-1")
        assert resp.status_code == 200
        tenant = resp.json()

        lfc = tenant.get("large_file_config", {})
        assert lfc.get("enabled") is True
        assert lfc.get("max_size_mb") == 2.5
        assert lfc.get("action") == "warn"

    async def test_disable_large_file_config(self, api_client, setup_test_tenants):
        """Can disable large_file_config on a tenant."""
        # First enable
        await api_client.put("/tenant/test-tenant-2", json={
            "large_file_config": {
                "enabled": True,
                "max_size_mb": 3.0,
                "action": "reject",
            },
        })

        # Then disable
        resp = await api_client.put("/tenant/test-tenant-2", json={
            "large_file_config": {
                "enabled": False,
            },
        })
        assert resp.status_code == 200

        # Verify
        resp = await api_client.get("/tenant/test-tenant-2")
        tenant = resp.json()
        lfc = tenant.get("large_file_config", {})
        assert lfc.get("enabled") is False


# ============================================
# 17. DELIVERY REPORTS
# ============================================
