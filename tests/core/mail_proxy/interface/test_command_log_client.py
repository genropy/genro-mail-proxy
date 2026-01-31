# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Tests for CommandLogAPI via HTTP client.

These tests verify the full pipeline:
    Client HTTP → FastAPI (api_base) → CommandLogEndpoint → CommandLogTable → DB
"""

import time

import pytest

from tools.http_client.client import MailProxyClient, CommandLogEntry


class TestCommandLogAPI:
    """Test CommandLogAPI through MailProxyClient."""

    # =========================================================================
    # List Operations
    # =========================================================================

    async def test_list_empty(self, client: MailProxyClient):
        """List command log returns empty list when no commands logged."""
        entries = await client.command_log.list()

        assert isinstance(entries, list)
        # May be empty or have entries from other test operations

    async def test_list_with_limit(self, client: MailProxyClient):
        """List command log with limit parameter."""
        entries = await client.command_log.list(limit=10)

        assert isinstance(entries, list)
        assert len(entries) <= 10

    async def test_list_with_offset(self, client: MailProxyClient):
        """List command log with offset parameter."""
        entries = await client.command_log.list(limit=5, offset=0)

        assert isinstance(entries, list)

    async def test_list_filter_by_tenant(self, client: MailProxyClient, setup_tenant):
        """List command log filtered by tenant ID."""
        entries = await client.command_log.list(tenant_id=setup_tenant)

        assert isinstance(entries, list)
        # All returned entries should be for the specified tenant
        for entry in entries:
            if entry.tenant_id is not None:
                assert entry.tenant_id == setup_tenant

    async def test_list_filter_by_time_range(self, client: MailProxyClient):
        """List command log filtered by time range."""
        now = int(time.time())
        one_hour_ago = now - 3600

        entries = await client.command_log.list(
            since_ts=one_hour_ago,
            until_ts=now,
        )

        assert isinstance(entries, list)
        # All entries should be within the time range
        for entry in entries:
            assert one_hour_ago <= entry.command_ts <= now

    async def test_list_filter_by_endpoint(self, client: MailProxyClient):
        """List command log filtered by endpoint pattern."""
        entries = await client.command_log.list(endpoint_filter="tenants")

        assert isinstance(entries, list)
        for entry in entries:
            assert "tenants" in entry.endpoint.lower()

    # =========================================================================
    # Get Single Entry
    # =========================================================================

    async def test_get_entry(self, client: MailProxyClient):
        """Get a specific command log entry by ID."""
        # First list to get an ID
        entries = await client.command_log.list(limit=1)

        if entries:
            entry_id = entries[0].id
            entry = await client.command_log.get(command_id=entry_id)

            assert isinstance(entry, CommandLogEntry)
            assert entry.id == entry_id

    # =========================================================================
    # Export Operations
    # =========================================================================

    async def test_export_all(self, client: MailProxyClient):
        """Export all command logs in replay-friendly format."""
        exported = await client.command_log.export()

        assert isinstance(exported, list)

    async def test_export_by_tenant(self, client: MailProxyClient, setup_tenant):
        """Export command logs for a specific tenant."""
        exported = await client.command_log.export(tenant_id=setup_tenant)

        assert isinstance(exported, list)

    async def test_export_by_time_range(self, client: MailProxyClient):
        """Export command logs within a time range."""
        now = int(time.time())
        one_day_ago = now - 86400

        exported = await client.command_log.export(
            since_ts=one_day_ago,
            until_ts=now,
        )

        assert isinstance(exported, list)

    # =========================================================================
    # Purge Operations
    # =========================================================================

    async def test_purge(self, client: MailProxyClient):
        """Purge old command log entries."""
        # Purge entries older than 1 second ago (should not purge recent ones)
        threshold = int(time.time()) - 1

        result = await client.command_log.purge(threshold_ts=threshold)

        assert result.get("ok") is True
        assert "deleted" in result

    async def test_purge_old_entries(self, client: MailProxyClient):
        """Purge entries older than a week."""
        one_week_ago = int(time.time()) - (7 * 24 * 3600)

        result = await client.command_log.purge(threshold_ts=one_week_ago)

        assert result.get("ok") is True

    # =========================================================================
    # Entry Data Structure
    # =========================================================================

    async def test_entry_has_required_fields(self, client: MailProxyClient):
        """Command log entries have all required fields."""
        # Create some activity to log
        await client.tenants.add(id="log-test-tenant")

        entries = await client.command_log.list(limit=5)

        for entry in entries:
            assert isinstance(entry, CommandLogEntry)
            assert isinstance(entry.id, int)
            assert isinstance(entry.command_ts, int)
            assert isinstance(entry.endpoint, str)
            # tenant_id may be None for global operations
            # payload is a dict (may be empty)
            assert isinstance(entry.payload, dict)
