# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Entity table tests with PostgreSQL backend.

These tests verify that all table operations work correctly with PostgreSQL,
covering the same functionality as SQLite tests but with real PostgreSQL.
"""

from __future__ import annotations

import time

import pytest

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


class TestTenantsTable:
    """Test TenantsTable with PostgreSQL."""

    async def test_add_tenant(self, pg_db):
        """Add a tenant."""
        tenants = pg_db.table("tenants")
        await tenants.add({"id": "t1", "name": "Test Tenant"})

        result = await tenants.get("t1")
        assert result is not None
        assert result["id"] == "t1"
        assert result["name"] == "Test Tenant"

    async def test_list_tenants(self, pg_db):
        """List all tenants."""
        tenants = pg_db.table("tenants")
        await tenants.add({"id": "t1", "name": "Tenant 1"})
        await tenants.add({"id": "t2", "name": "Tenant 2"})

        result = await tenants.list_all()
        assert len(result) == 2
        ids = {t["id"] for t in result}
        assert ids == {"t1", "t2"}

    async def test_update_tenant(self, pg_db):
        """Update a tenant."""
        tenants = pg_db.table("tenants")
        await tenants.add({"id": "t1", "name": "Original"})
        await tenants.update({"name": "Updated"}, {"id": "t1"})

        result = await tenants.get("t1")
        assert result["name"] == "Updated"

    async def test_remove_tenant(self, pg_db):
        """Remove a tenant."""
        tenants = pg_db.table("tenants")
        await tenants.add({"id": "t1", "name": "Test"})
        await tenants.remove("t1")

        result = await tenants.get("t1")
        assert result is None


class TestAccountsTable:
    """Test AccountsTable with PostgreSQL."""

    async def test_add_account(self, pg_db):
        """Add an account."""
        tenants = pg_db.table("tenants")
        accounts = pg_db.table("accounts")

        await tenants.add({"id": "t1", "name": "Test"})
        await accounts.add({
            "id": "acc1",
            "tenant_id": "t1",
            "host": "smtp.example.com",
            "port": 587,
            "user": "user@example.com",
        })

        result = await accounts.get("t1", "acc1")
        assert result is not None
        assert result["host"] == "smtp.example.com"

    async def test_list_accounts_by_tenant(self, pg_db):
        """List accounts filtered by tenant."""
        tenants = pg_db.table("tenants")
        accounts = pg_db.table("accounts")

        await tenants.add({"id": "t1", "name": "Tenant 1"})
        await tenants.add({"id": "t2", "name": "Tenant 2"})
        await accounts.add({"id": "acc1", "tenant_id": "t1", "host": "smtp1.example.com", "port": 587})
        await accounts.add({"id": "acc2", "tenant_id": "t2", "host": "smtp2.example.com", "port": 587})

        result = await accounts.list_all(tenant_id="t1")
        assert len(result) == 1
        assert result[0]["id"] == "acc1"


class TestMessagesTable:
    """Test MessagesTable with PostgreSQL."""

    async def _setup(self, pg_db):
        """Create tenant and account for messages."""
        await pg_db.table("tenants").add({"id": "t1", "name": "Test"})
        await pg_db.table("accounts").add({
            "id": "acc1",
            "tenant_id": "t1",
            "host": "smtp.example.com",
            "port": 587,
        })

    async def test_insert_batch(self, pg_db):
        """Insert a batch of messages."""
        await self._setup(pg_db)
        messages = pg_db.table("messages")

        result = await messages.insert_batch([
            {
                "id": "msg1",
                "tenant_id": "t1",
                "account_id": "acc1",
                "payload": {"to": "a@b.com", "subject": "Test"},
            },
            {
                "id": "msg2",
                "tenant_id": "t1",
                "account_id": "acc1",
                "payload": {"to": "c@d.com", "subject": "Test 2"},
            },
        ])

        assert len(result) == 2
        assert all("pk" in r for r in result)

    async def test_fetch_ready(self, pg_db):
        """Fetch messages ready for delivery."""
        await self._setup(pg_db)
        messages = pg_db.table("messages")

        await messages.insert_batch([
            {
                "id": "msg1",
                "tenant_id": "t1",
                "account_id": "acc1",
                "payload": {"to": "a@b.com", "subject": "Test"},
            },
        ])

        now = int(time.time())
        ready = await messages.fetch_ready(limit=10, now_ts=now + 1)
        assert len(ready) == 1
        assert ready[0]["id"] == "msg1"

    async def test_mark_sent(self, pg_db):
        """Mark a message as sent."""
        await self._setup(pg_db)
        messages = pg_db.table("messages")

        inserted = await messages.insert_batch([
            {
                "id": "msg1",
                "tenant_id": "t1",
                "account_id": "acc1",
                "payload": {"to": "a@b.com", "subject": "Test"},
            },
        ])
        pk = inserted[0]["pk"]

        sent_ts = int(time.time())
        await messages.mark_sent(pk, sent_ts)

        # Should not appear in fetch_ready
        ready = await messages.fetch_ready(limit=10, now_ts=sent_ts + 1)
        assert len(ready) == 0

    async def test_mark_error(self, pg_db):
        """Mark a message with error (sets smtp_ts)."""
        await self._setup(pg_db)
        messages = pg_db.table("messages")

        inserted = await messages.insert_batch([
            {
                "id": "msg1",
                "tenant_id": "t1",
                "account_id": "acc1",
                "payload": {"to": "a@b.com", "subject": "Test"},
            },
        ])
        pk = inserted[0]["pk"]

        error_ts = int(time.time())
        await messages.mark_error(pk, error_ts)

        # Verify message has smtp_ts set
        msg = await messages.select_one(where={"pk": pk})
        assert msg["smtp_ts"] == error_ts


class TestMessageEventTable:
    """Test MessageEventTable with PostgreSQL."""

    async def _setup(self, pg_db):
        """Create tenant, account, and message."""
        from genro_toolbox import get_uuid

        await pg_db.table("tenants").add({"id": "t1", "name": "Test"})
        await pg_db.table("accounts").add({
            "id": "acc1",
            "tenant_id": "t1",
            "host": "smtp.example.com",
            "port": 587,
        })

        pk = get_uuid()
        await pg_db.table("messages").insert({
            "pk": pk,
            "id": "msg1",
            "tenant_id": "t1",
            "account_id": "acc1",
            "payload": '{"to": "a@b.com"}',
        })
        return pk

    async def test_add_event(self, pg_db):
        """Add an event for a message."""
        msg_pk = await self._setup(pg_db)
        events = pg_db.table("message_events")

        await events.add_event(
            message_pk=msg_pk,
            event_type="sent",
            event_ts=int(time.time()),
        )

        msg_events = await events.get_events_for_message(msg_pk)
        assert len(msg_events) == 1
        assert msg_events[0]["event_type"] == "sent"

    async def test_fetch_unreported(self, pg_db):
        """Fetch unreported events."""
        msg_pk = await self._setup(pg_db)
        events = pg_db.table("message_events")

        ts = int(time.time())
        await events.add_event(message_pk=msg_pk, event_type="sent", event_ts=ts)

        unreported = await events.fetch_unreported(limit=10)
        assert len(unreported) == 1

    async def test_mark_reported(self, pg_db):
        """Mark events as reported."""
        msg_pk = await self._setup(pg_db)
        events = pg_db.table("message_events")

        ts = int(time.time())
        await events.add_event(message_pk=msg_pk, event_type="sent", event_ts=ts)

        unreported = await events.fetch_unreported(limit=10)
        event_ids = [e["event_id"] for e in unreported]

        await events.mark_reported(event_ids, int(time.time()))

        # Should no longer be unreported
        unreported_after = await events.fetch_unreported(limit=10)
        assert len(unreported_after) == 0


class TestConcurrentAccess:
    """Test concurrent database operations with PostgreSQL."""

    async def test_concurrent_inserts(self, pg_db):
        """Multiple concurrent inserts don't conflict."""
        import asyncio

        await pg_db.table("tenants").add({"id": "t1", "name": "Test"})
        await pg_db.table("accounts").add({
            "id": "acc1",
            "tenant_id": "t1",
            "host": "smtp.example.com",
            "port": 587,
        })

        messages = pg_db.table("messages")

        async def insert_msg(i: int):
            return await messages.insert_batch([{
                "id": f"concurrent-{i}",
                "tenant_id": "t1",
                "account_id": "acc1",
                "payload": {"to": f"user{i}@example.com", "subject": f"Test {i}"},
            }])

        results = await asyncio.gather(*[insert_msg(i) for i in range(10)])

        all_inserted = [pk for result in results for pk in result]
        assert len(all_inserted) == 10

    async def test_concurrent_status_updates(self, pg_db):
        """Concurrent status updates are handled correctly."""
        import asyncio

        await pg_db.table("tenants").add({"id": "t1", "name": "Test"})
        await pg_db.table("accounts").add({
            "id": "acc1",
            "tenant_id": "t1",
            "host": "smtp.example.com",
            "port": 587,
        })

        messages = pg_db.table("messages")

        # Create messages
        pks = []
        for i in range(5):
            inserted = await messages.insert_batch([{
                "id": f"status-{i}",
                "tenant_id": "t1",
                "account_id": "acc1",
                "payload": {"to": f"user{i}@example.com", "subject": f"Test {i}"},
            }])
            pks.append(inserted[0]["pk"])

        # Concurrent mark_sent
        sent_ts = int(time.time())

        async def mark_sent(pk: str):
            await messages.mark_sent(pk, sent_ts)

        await asyncio.gather(*[mark_sent(pk) for pk in pks])

        # Verify all marked
        all_msgs = await messages.list_all()
        for msg in all_msgs:
            if msg["id"].startswith("status-"):
                assert msg["smtp_ts"] == sent_ts


class TestPostgreSQLSpecific:
    """Test PostgreSQL-specific behaviors."""

    async def test_unicode_handling(self, pg_db):
        """Unicode characters are handled correctly."""
        tenants = pg_db.table("tenants")

        await tenants.add({
            "id": "unicode",
            "name": "Test 日本語 🚀 émojis",
        })

        result = await tenants.get("unicode")
        assert result["name"] == "Test 日本語 🚀 émojis"

    async def test_null_vs_empty_string(self, pg_db):
        """NULL and empty string are distinct."""
        tenants = pg_db.table("tenants")

        await tenants.add({
            "id": "null-test",
            "name": "",  # empty string
            "client_base_url": None,  # NULL
        })

        result = await tenants.get("null-test")
        assert result["name"] == ""
        assert result["client_base_url"] is None

    async def test_json_fields(self, pg_db):
        """JSON fields are stored and retrieved correctly."""
        tenants = pg_db.table("tenants")

        await tenants.add({
            "id": "json-test",
            "name": "Test",
            "client_auth": {
                "method": "bearer",
                "token": "secret",
                "nested": {"key": "value"},
            },
        })

        result = await tenants.get("json-test")
        assert result["client_auth"]["method"] == "bearer"
        assert result["client_auth"]["nested"]["key"] == "value"
