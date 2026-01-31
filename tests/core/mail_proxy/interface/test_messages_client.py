# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Tests for MessagesAPI via HTTP client.

These tests verify the full pipeline:
    Client HTTP → FastAPI (api_base) → MessageEndpoint → MessagesTable → DB
"""

import pytest

from tools.http_client.client import MailProxyClient, Message


class TestMessagesAPI:
    """Test MessagesAPI through MailProxyClient."""

    # =========================================================================
    # Basic CRUD Operations
    # =========================================================================

    async def test_add_message(self, client: MailProxyClient, setup_account):
        """Add a single message to the queue."""
        tenant_id, account_id = setup_account

        result = await client.messages.add(
            id="msg-001",
            tenant_id=tenant_id,
            account_id=account_id,
            from_addr="sender@test.local",
            to=["recipient@test.local"],
            subject="Test Subject",
            body="Test body content",
        )

        assert result["id"] == "msg-001"
        assert "pk" in result  # Internal primary key is returned

    async def test_add_message_with_all_fields(self, client: MailProxyClient, setup_account):
        """Add message with all optional fields."""
        tenant_id, account_id = setup_account

        result = await client.messages.add(
            id="msg-full",
            tenant_id=tenant_id,
            account_id=account_id,
            from_addr="sender@test.local",
            to=["to@test.local"],
            subject="Full Test",
            body="<html><body>Hello</body></html>",
            cc=["cc@test.local"],
            bcc=["bcc@test.local"],
            reply_to="reply@test.local",
            content_type="html",
            priority=1,
            batch_code="campaign-001",
            headers={"X-Custom": "value"},
        )

        assert result["id"] == "msg-full"
        assert "pk" in result  # Internal primary key is returned

    async def test_get_message(self, client: MailProxyClient, setup_account):
        """Retrieve a message by ID."""
        tenant_id, account_id = setup_account

        # Add message
        await client.messages.add(
            id="msg-get",
            tenant_id=tenant_id,
            account_id=account_id,
            from_addr="a@b.com",
            to=["c@d.com"],
            subject="Get Test",
            body="body",
        )

        # Get message
        msg = await client.messages.get(message_id="msg-get", tenant_id=tenant_id)

        assert isinstance(msg, Message)
        assert msg.id == "msg-get"
        assert msg.subject == "Get Test"
        assert msg.status == "pending"

    async def test_list_messages_empty(self, client: MailProxyClient, setup_tenant):
        """List messages returns empty list when no messages."""
        messages = await client.messages.list(tenant_id=setup_tenant)

        assert messages == []

    async def test_list_messages(self, client: MailProxyClient, setup_account):
        """List all messages for a tenant."""
        tenant_id, account_id = setup_account

        # Add two messages
        await client.messages.add(
            id="msg-1",
            tenant_id=tenant_id,
            account_id=account_id,
            from_addr="a@b.com",
            to=["c@d.com"],
            subject="First",
            body="1",
        )
        await client.messages.add(
            id="msg-2",
            tenant_id=tenant_id,
            account_id=account_id,
            from_addr="a@b.com",
            to=["c@d.com"],
            subject="Second",
            body="2",
        )

        messages = await client.messages.list(tenant_id=tenant_id)

        assert len(messages) == 2
        assert all(isinstance(m, Message) for m in messages)
        ids = {m.id for m in messages}
        assert ids == {"msg-1", "msg-2"}

    async def test_list_messages_active_only(self, client: MailProxyClient, setup_account):
        """List only active (pending/deferred) messages."""
        tenant_id, account_id = setup_account

        await client.messages.add(
            id="msg-active",
            tenant_id=tenant_id,
            account_id=account_id,
            from_addr="a@b.com",
            to=["c@d.com"],
            subject="Active",
            body="body",
        )

        messages = await client.messages.list(tenant_id=tenant_id, active_only=True)

        assert len(messages) >= 1
        # All returned messages should be active (pending or deferred)
        for msg in messages:
            assert msg.status in ("pending", "deferred")

    # =========================================================================
    # Batch Operations
    # =========================================================================

    async def test_add_batch(self, client: MailProxyClient, setup_account):
        """Add multiple messages in a single call."""
        tenant_id, account_id = setup_account

        messages = [
            {
                "id": f"batch-{i}",
                "tenant_id": tenant_id,
                "account_id": account_id,
                "from_addr": "sender@test.local",
                "to": [f"recipient{i}@test.local"],
                "subject": f"Batch Message {i}",
                "body": f"Body {i}",
            }
            for i in range(3)
        ]

        result = await client.messages.add_batch(messages=messages)

        assert result.get("ok") is True
        assert result.get("queued") == 3

    async def test_add_batch_with_default_priority(self, client: MailProxyClient, setup_account):
        """Add batch with default priority for all messages."""
        tenant_id, account_id = setup_account

        messages = [
            {
                "id": "batch-prio-1",
                "tenant_id": tenant_id,
                "account_id": account_id,
                "from_addr": "a@b.com",
                "to": ["c@d.com"],
                "subject": "High Priority",
                "body": "body",
            }
        ]

        result = await client.messages.add_batch(messages=messages, default_priority=1)

        assert result.get("ok") is True

    async def test_delete_batch(self, client: MailProxyClient, setup_account):
        """Delete multiple messages by IDs."""
        tenant_id, account_id = setup_account

        # Add messages
        for i in range(3):
            await client.messages.add(
                id=f"del-{i}",
                tenant_id=tenant_id,
                account_id=account_id,
                from_addr="a@b.com",
                to=["c@d.com"],
                subject=f"To Delete {i}",
                body="body",
            )

        # Delete batch
        result = await client.messages.delete_batch(
            tenant_id=tenant_id,
            ids=["del-0", "del-1"],
        )

        assert result.get("ok") is True

        # Verify only one remains
        messages = await client.messages.list(tenant_id=tenant_id)
        ids = {m.id for m in messages}
        assert "del-2" in ids
        assert "del-0" not in ids
        assert "del-1" not in ids

    # =========================================================================
    # Delete and Cleanup
    # =========================================================================

    async def test_delete_message(self, client: MailProxyClient, setup_account):
        """Delete a single message by primary key."""
        tenant_id, account_id = setup_account

        # Add message
        result = await client.messages.add(
            id="to-delete",
            tenant_id=tenant_id,
            account_id=account_id,
            from_addr="a@b.com",
            to=["c@d.com"],
            subject="Delete Me",
            body="body",
        )
        pk = result["pk"]

        # Delete by pk
        success = await client.messages.delete(message_pk=pk)
        assert success is True

        # Verify gone
        messages = await client.messages.list(tenant_id=tenant_id)
        assert all(m.pk != pk for m in messages)

    async def test_cleanup(self, client: MailProxyClient, setup_tenant):
        """Cleanup old reported messages."""
        result = await client.messages.cleanup(
            tenant_id=setup_tenant,
            older_than_seconds=3600,
        )

        assert result.get("ok") is True
        assert "removed" in result

    # =========================================================================
    # Count Operations
    # =========================================================================

    async def test_count_active(self, client: MailProxyClient, setup_account):
        """Count total active messages across all tenants."""
        tenant_id, account_id = setup_account

        # Add some messages
        await client.messages.add(
            id="count-1",
            tenant_id=tenant_id,
            account_id=account_id,
            from_addr="a@b.com",
            to=["c@d.com"],
            subject="Count Test",
            body="body",
        )

        count = await client.messages.count_active()

        assert isinstance(count, int)
        assert count >= 1

    async def test_count_pending_for_tenant(self, client: MailProxyClient, setup_account):
        """Count pending messages for a specific tenant."""
        tenant_id, account_id = setup_account

        # Add messages
        for i in range(2):
            await client.messages.add(
                id=f"count-tenant-{i}",
                tenant_id=tenant_id,
                account_id=account_id,
                from_addr="a@b.com",
                to=["c@d.com"],
                subject=f"Count {i}",
                body="body",
            )

        count = await client.messages.count_pending_for_tenant(tenant_id=tenant_id)

        assert isinstance(count, int)
        assert count >= 2

    async def test_count_pending_by_batch_code(self, client: MailProxyClient, setup_account):
        """Count pending messages for a specific batch."""
        tenant_id, account_id = setup_account

        # Add messages with batch code
        await client.messages.add(
            id="batch-count-1",
            tenant_id=tenant_id,
            account_id=account_id,
            from_addr="a@b.com",
            to=["c@d.com"],
            subject="Batch Count",
            body="body",
            batch_code="campaign-x",
        )

        count = await client.messages.count_pending_for_tenant(
            tenant_id=tenant_id,
            batch_code="campaign-x",
        )

        assert isinstance(count, int)
        assert count >= 1
