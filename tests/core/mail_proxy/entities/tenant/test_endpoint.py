# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Tests for TenantEndpoint - direct endpoint tests for coverage.

These tests directly exercise TenantEndpoint methods and helper functions
to cover edge cases and error paths.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from core.mail_proxy.entities.tenant.endpoint import (
    TenantEndpoint,
    AuthMethod,
    LargeFileAction,
    get_tenant_sync_url,
    get_tenant_attachment_url,
    DEFAULT_SYNC_PATH,
    DEFAULT_ATTACHMENT_PATH,
)


@pytest.fixture
def mock_table():
    """Create mock TenantsTable."""
    table = MagicMock()
    table.add = AsyncMock(return_value=None)
    table.get = AsyncMock(return_value={"id": "t1", "name": "Test"})
    table.list_all = AsyncMock(return_value=[])
    table.remove = AsyncMock(return_value=True)
    table.update_fields = AsyncMock()
    table.suspend_batch = AsyncMock(return_value=True)
    table.activate_batch = AsyncMock(return_value=True)
    table.get_suspended_batches = AsyncMock(return_value=set())
    return table


@pytest.fixture
def endpoint(mock_table):
    """Create TenantEndpoint with mock table."""
    return TenantEndpoint(mock_table)


class TestAuthMethodEnum:
    """Tests for AuthMethod enum values."""

    def test_auth_method_none(self):
        """AuthMethod.NONE has correct value."""
        assert AuthMethod.NONE.value == "none"

    def test_auth_method_bearer(self):
        """AuthMethod.BEARER has correct value."""
        assert AuthMethod.BEARER.value == "bearer"

    def test_auth_method_basic(self):
        """AuthMethod.BASIC has correct value."""
        assert AuthMethod.BASIC.value == "basic"


class TestLargeFileActionEnum:
    """Tests for LargeFileAction enum values."""

    def test_action_warn(self):
        """LargeFileAction.WARN has correct value."""
        assert LargeFileAction.WARN.value == "warn"

    def test_action_reject(self):
        """LargeFileAction.REJECT has correct value."""
        assert LargeFileAction.REJECT.value == "reject"

    def test_action_rewrite(self):
        """LargeFileAction.REWRITE has correct value."""
        assert LargeFileAction.REWRITE.value == "rewrite"


class TestGetTenantSyncUrl:
    """Tests for get_tenant_sync_url() helper function."""

    def test_sync_url_with_custom_path(self):
        """get_tenant_sync_url() uses custom sync path."""
        tenant = {
            "client_base_url": "https://acme.com",
            "client_sync_path": "/api/custom-sync",
        }
        url = get_tenant_sync_url(tenant)
        assert url == "https://acme.com/api/custom-sync"

    def test_sync_url_with_default_path(self):
        """get_tenant_sync_url() uses default sync path when not specified."""
        tenant = {"client_base_url": "https://acme.com"}
        url = get_tenant_sync_url(tenant)
        assert url == f"https://acme.com{DEFAULT_SYNC_PATH}"

    def test_sync_url_strips_trailing_slash(self):
        """get_tenant_sync_url() strips trailing slash from base URL."""
        tenant = {"client_base_url": "https://acme.com/"}
        url = get_tenant_sync_url(tenant)
        assert url == f"https://acme.com{DEFAULT_SYNC_PATH}"

    def test_sync_url_returns_none_without_base_url(self):
        """get_tenant_sync_url() returns None when no base_url."""
        tenant = {"client_sync_path": "/api/sync"}
        url = get_tenant_sync_url(tenant)
        assert url is None

    def test_sync_url_returns_none_for_empty_base_url(self):
        """get_tenant_sync_url() returns None for empty base_url."""
        tenant = {"client_base_url": "", "client_sync_path": "/api/sync"}
        url = get_tenant_sync_url(tenant)
        assert url is None


class TestGetTenantAttachmentUrl:
    """Tests for get_tenant_attachment_url() helper function."""

    def test_attachment_url_with_custom_path(self):
        """get_tenant_attachment_url() uses custom attachment path."""
        tenant = {
            "client_base_url": "https://acme.com",
            "client_attachment_path": "/files/download",
        }
        url = get_tenant_attachment_url(tenant)
        assert url == "https://acme.com/files/download"

    def test_attachment_url_with_default_path(self):
        """get_tenant_attachment_url() uses default attachment path."""
        tenant = {"client_base_url": "https://acme.com"}
        url = get_tenant_attachment_url(tenant)
        assert url == f"https://acme.com{DEFAULT_ATTACHMENT_PATH}"

    def test_attachment_url_returns_none_without_base_url(self):
        """get_tenant_attachment_url() returns None without base_url."""
        tenant = {"client_attachment_path": "/files"}
        url = get_tenant_attachment_url(tenant)
        assert url is None

    def test_attachment_url_returns_none_for_empty_base_url(self):
        """get_tenant_attachment_url() returns None for empty base_url."""
        tenant = {"client_base_url": ""}
        url = get_tenant_attachment_url(tenant)
        assert url is None


class TestTenantEndpointGet:
    """Tests for TenantEndpoint.get() method."""

    async def test_get_not_found_raises(self, endpoint, mock_table):
        """get() raises ValueError when tenant not found."""
        mock_table.get = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="Tenant 'nonexistent' not found"):
            await endpoint.get("nonexistent")


class TestTenantEndpointSuspendBatch:
    """Tests for TenantEndpoint.suspend_batch() method."""

    async def test_suspend_batch_not_found_raises(self, endpoint, mock_table):
        """suspend_batch() raises ValueError when tenant not found."""
        mock_table.suspend_batch = AsyncMock(return_value=False)
        with pytest.raises(ValueError, match="Tenant 't1' not found"):
            await endpoint.suspend_batch("t1", "batch-001")


class TestTenantEndpointActivateBatch:
    """Tests for TenantEndpoint.activate_batch() method."""

    async def test_activate_batch_tenant_not_found(self, endpoint, mock_table):
        """activate_batch() raises ValueError when tenant not found."""
        mock_table.activate_batch = AsyncMock(return_value=False)
        mock_table.get = AsyncMock(return_value=None)

        with pytest.raises(ValueError, match="Tenant 'missing' not found"):
            await endpoint.activate_batch("missing", "batch-001")

    async def test_activate_batch_cannot_remove_single_from_all(self, endpoint, mock_table):
        """activate_batch() raises ValueError when trying to remove single from '*'."""
        mock_table.activate_batch = AsyncMock(return_value=False)
        mock_table.get = AsyncMock(return_value={"id": "t1", "name": "Test"})

        with pytest.raises(ValueError, match="Cannot remove single batch"):
            await endpoint.activate_batch("t1", "specific-batch")


class TestTenantEndpointGetSuspendedBatches:
    """Tests for TenantEndpoint.get_suspended_batches() method."""

    async def test_get_suspended_batches_not_found(self, endpoint, mock_table):
        """get_suspended_batches() raises ValueError when tenant not found."""
        mock_table.get = AsyncMock(return_value=None)

        with pytest.raises(ValueError, match="Tenant 'missing' not found"):
            await endpoint.get_suspended_batches("missing")

    async def test_get_suspended_batches_success(self, endpoint, mock_table):
        """get_suspended_batches() returns suspended batches list."""
        mock_table.get = AsyncMock(return_value={"id": "t1", "name": "Test"})
        mock_table.get_suspended_batches = AsyncMock(return_value={"batch-a", "batch-b"})

        result = await endpoint.get_suspended_batches("t1")

        assert result["ok"] is True
        assert result["tenant_id"] == "t1"
        assert set(result["suspended_batches"]) == {"batch-a", "batch-b"}
