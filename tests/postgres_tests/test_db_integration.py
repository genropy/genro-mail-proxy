# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Database integration tests using testcontainers PostgreSQL.

These tests validate real PostgreSQL behavior that cannot be fully tested
with SQLite or mocks:
- Unicode/emoji handling
- Foreign key constraint enforcement
- Concurrent access patterns
- PostgreSQL-specific SQL syntax (casts, etc.)
"""

import asyncio

import pytest

pytestmark = [pytest.mark.db, pytest.mark.asyncio]


class TestUnicodeHandling:
    """Verify Unicode and emoji characters are handled correctly."""

    async def test_unicode_in_tenant_name(self, pg_db):
        """Emoji and unicode in tenant names are stored and retrieved correctly."""
        tenants = pg_db.table("tenants")

        await tenants.add({
            "id": "unicode-test",
            "name": "Acme Corp 🚀 Intl",
            "active": True,
        })

        retrieved = await tenants.get("unicode-test")
        assert retrieved is not None
        assert retrieved["name"] == "Acme Corp 🚀 Intl"

    async def test_unicode_in_account_fields(self, pg_db):
        """Unicode in account configuration."""
        tenants = pg_db.table("tenants")
        accounts = pg_db.table("accounts")

        await tenants.add({"id": "t1", "name": "Test"})
        await accounts.add({
            "id": "acc-unicode",
            "tenant_id": "t1",
            "host": "smtp.example.com",
            "port": 587,
            "user": "user@日本.com",
        })

        retrieved = await accounts.get("acc-unicode")
        assert retrieved["user"] == "user@日本.com"

    async def test_json_field_with_unicode(self, pg_db):
        """JSON fields preserve unicode in nested structures."""
        tenants = pg_db.table("tenants")

        await tenants.add({
            "id": "json-unicode",
            "name": "Test",
            "client_auth": {"method": "bearer", "token": "秘密トークン🔐"},
        })

        retrieved = await tenants.get("json-unicode")
        assert retrieved["client_auth"]["token"] == "秘密トークン🔐"


class TestForeignKeyConstraints:
    """Verify FK constraints are enforced by PostgreSQL."""

    async def test_account_requires_valid_tenant(self, pg_db):
        """Account with non-existent tenant_id is rejected."""
        accounts = pg_db.table("accounts")

        # Try to insert account with non-existent tenant
        # Should fail due to FK constraint
        with pytest.raises(Exception) as exc_info:
            await accounts.add({
                "id": "orphan-account",
                "tenant_id": "nonexistent-tenant",
                "host": "smtp.example.com",
                "port": 587,
            })

        # PostgreSQL raises IntegrityError for FK violations
        assert "foreign key" in str(exc_info.value).lower() or "violates" in str(exc_info.value).lower()

    async def test_cascade_behavior(self, pg_db):
        """Verify FK constraint prevents tenant deletion when accounts exist.

        Our schema uses RESTRICT (not CASCADE) for FK constraints, so deleting
        a tenant with accounts should fail with a ForeignKeyViolation.
        """
        tenants = pg_db.table("tenants")
        accounts = pg_db.table("accounts")

        await tenants.add({"id": "cascade-test", "name": "Cascade Test"})
        await accounts.add({
            "id": "cascade-acc",
            "tenant_id": "cascade-test",
            "host": "smtp.example.com",
            "port": 587,
        })

        # Attempt to delete tenant - should fail due to FK constraint
        import psycopg.errors

        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            await tenants.remove("cascade-test")

        # Tenant and account should still exist
        assert await tenants.get("cascade-test") is not None
        acc_list = await accounts.list_all(tenant_id="cascade-test")
        assert len(acc_list) == 1


class TestConcurrentAccess:
    """Test concurrent database operations."""

    async def test_concurrent_message_inserts(self, pg_db):
        """Multiple concurrent inserts don't cause conflicts."""
        tenants = pg_db.table("tenants")
        accounts = pg_db.table("accounts")
        messages = pg_db.table("messages")

        # Setup
        await tenants.add({"id": "concurrent", "name": "Concurrent Test"})
        await accounts.add({
            "id": "concurrent-acc",
            "tenant_id": "concurrent",
            "host": "smtp.example.com",
            "port": 587,
        })

        # Concurrent inserts
        async def insert_message(i: int):
            return await messages.insert_batch([{
                "id": f"msg-concurrent-{i}",
                "tenant_id": "concurrent",
                "account_id": "concurrent-acc",
                "priority": 2,
                "payload": {"subject": f"Test {i}", "body": "Body"},
            }])

        # Run 10 concurrent inserts
        results = await asyncio.gather(*[insert_message(i) for i in range(10)])

        # All should succeed
        all_inserted = [msg_id for result in results for msg_id in result]
        assert len(all_inserted) == 10

        # Verify all messages exist
        all_msgs = await messages.list_all()
        msg_ids = {m["id"] for m in all_msgs}
        for i in range(10):
            assert f"msg-concurrent-{i}" in msg_ids

    async def test_concurrent_status_updates(self, pg_db):
        """Concurrent status updates are handled correctly."""
        tenants = pg_db.table("tenants")
        accounts = pg_db.table("accounts")
        messages = pg_db.table("messages")

        # Setup
        await tenants.add({"id": "status", "name": "Status Test"})
        await accounts.add({
            "id": "status-acc",
            "tenant_id": "status",
            "host": "smtp.example.com",
            "port": 587,
        })

        # Create multiple messages and store pks
        pks = {}
        for i in range(5):
            inserted = await messages.insert_batch([{
                "id": f"msg-status-{i}",
                "tenant_id": "status",
                "account_id": "status-acc",
                "priority": 2,
                "payload": {"subject": f"Test {i}", "body": "Body"},
            }])
            pks[f"msg-status-{i}"] = inserted[0]["pk"]

        # Concurrent mark_sent operations
        import time
        now = int(time.time())

        async def mark_message_sent(msg_id: str):
            await messages.mark_sent(pks[msg_id], msg_id, now)

        await asyncio.gather(*[
            mark_message_sent(f"msg-status-{i}") for i in range(5)
        ])

        # Verify all are marked as sent
        all_msgs = await messages.list_all()
        for msg in all_msgs:
            if msg["id"].startswith("msg-status-"):
                assert msg["smtp_ts"] == now


class TestPostgreSQLSpecificBehavior:
    """Test PostgreSQL-specific features and syntax."""

    async def test_timestamp_handling(self, pg_db):
        """PostgreSQL timestamp functions work correctly."""
        tenants = pg_db.table("tenants")

        await tenants.add({"id": "ts-test", "name": "Timestamp Test"})

        retrieved = await tenants.get("ts-test")
        assert retrieved is not None
        # created_at should be auto-populated
        assert retrieved["created_at"] is not None

    async def test_null_vs_empty_string(self, pg_db):
        """PostgreSQL correctly distinguishes NULL from empty string."""
        tenants = pg_db.table("tenants")

        await tenants.add({
            "id": "null-test",
            "name": "",  # Empty string
            "client_base_url": None,  # NULL
        })

        retrieved = await tenants.get("null-test")
        assert retrieved["name"] == ""  # Empty string preserved
        assert retrieved["client_base_url"] is None  # NULL preserved

    async def test_integer_boundaries(self, pg_db):
        """PostgreSQL handles large integers correctly."""
        accounts = pg_db.table("accounts")
        tenants = pg_db.table("tenants")

        await tenants.add({"id": "int-test", "name": "Int Test"})

        # Test with large rate limit values
        await accounts.add({
            "id": "large-limits",
            "tenant_id": "int-test",
            "host": "smtp.example.com",
            "port": 587,
            "limit_per_day": 1000000,  # 1 million
        })

        retrieved = await accounts.get("large-limits")
        assert retrieved["limit_per_day"] == 1000000


class TestUpsertBehavior:
    """Test INSERT ... ON CONFLICT behavior."""

    async def test_upsert_creates_new_record(self, pg_db):
        """Upsert creates record when it doesn't exist."""
        tenants = pg_db.table("tenants")

        await tenants.add({"id": "upsert-new", "name": "New Tenant"})

        retrieved = await tenants.get("upsert-new")
        assert retrieved is not None
        assert retrieved["name"] == "New Tenant"

    async def test_upsert_updates_existing_record(self, pg_db):
        """Upsert updates record when it already exists."""
        tenants = pg_db.table("tenants")

        # Create
        await tenants.add({"id": "upsert-update", "name": "Original Name"})

        # Update via upsert
        await tenants.add({"id": "upsert-update", "name": "Updated Name"})

        retrieved = await tenants.get("upsert-update")
        assert retrieved["name"] == "Updated Name"

    async def test_message_upsert_preserves_sent_status(self, pg_db):
        """Message upsert doesn't overwrite sent messages."""
        tenants = pg_db.table("tenants")
        accounts = pg_db.table("accounts")
        messages = pg_db.table("messages")

        await tenants.add({"id": "msg-upsert", "name": "Test"})
        await accounts.add({
            "id": "msg-upsert-acc",
            "tenant_id": "msg-upsert",
            "host": "smtp.example.com",
            "port": 587,
        })

        # Insert message
        inserted = await messages.insert_batch([{
            "id": "msg-to-preserve",
            "tenant_id": "msg-upsert",
            "account_id": "msg-upsert-acc",
            "priority": 2,
            "payload": {"subject": "Original", "body": "Body"},
        }])
        pk = inserted[0]["pk"]

        # Mark as sent
        import time
        sent_ts = int(time.time())
        await messages.mark_sent(pk, sent_ts)

        # Try to upsert - should not overwrite
        result = await messages.insert_batch([{
            "id": "msg-to-preserve",
            "tenant_id": "msg-upsert",
            "account_id": "msg-upsert-acc",
            "priority": 1,  # Different priority
            "payload": {"subject": "New", "body": "New Body"},
        }])

        # Should not be inserted (already sent)
        assert len(result) == 0

        # Original should be preserved
        retrieved = await messages.list_all()
        msg = next(m for m in retrieved if m["id"] == "msg-to-preserve")
        assert msg["smtp_ts"] == sent_ts
        assert msg["message"]["subject"] == "Original"


class TestQueryPatterns:
    """Test various query patterns work correctly with PostgreSQL."""

    async def test_in_clause_with_many_ids(self, pg_db):
        """IN clause works with large number of IDs."""
        tenants = pg_db.table("tenants")
        accounts = pg_db.table("accounts")
        messages = pg_db.table("messages")

        await tenants.add({"id": "in-clause", "name": "Test"})
        await accounts.add({
            "id": "in-clause-acc",
            "tenant_id": "in-clause",
            "host": "smtp.example.com",
            "port": 587,
        })

        # Create 50 messages
        for i in range(50):
            await messages.insert_batch([{
                "id": f"in-msg-{i}",
                "tenant_id": "in-clause",
                "account_id": "in-clause-acc",
                "priority": 2,
                "payload": {"subject": f"Test {i}", "body": "Body"},
            }])

        # Query with many IDs
        ids = [f"in-msg-{i}" for i in range(50)]
        existing = await messages.existing_ids(ids)

        assert len(existing) == 50

    async def test_order_by_multiple_columns(self, pg_db):
        """ORDER BY with multiple columns works correctly."""
        tenants = pg_db.table("tenants")
        accounts = pg_db.table("accounts")
        messages = pg_db.table("messages")

        await tenants.add({"id": "order", "name": "Test"})
        await accounts.add({
            "id": "order-acc",
            "tenant_id": "order",
            "host": "smtp.example.com",
            "port": 587,
        })

        # Create messages with different priorities
        import time
        now = int(time.time())

        for priority in [3, 1, 2]:
            await messages.insert_batch([{
                "id": f"order-msg-{priority}",
                "tenant_id": "order",
                "account_id": "order-acc",
                "priority": priority,
                "payload": {"subject": f"Priority {priority}", "body": "Body"},
            }])

        # Fetch ready should return in priority order
        ready = await messages.fetch_ready(limit=10, now_ts=now + 1)

        priorities = [m["priority"] for m in ready if m["id"].startswith("order-msg")]
        # Should be sorted by priority ASC
        assert priorities == sorted(priorities)
