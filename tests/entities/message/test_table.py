# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Tests for MessagesTable - CE table methods."""

import time

import pytest

from core.mail_proxy.proxy_base import MailProxyBase


@pytest.fixture
async def db(tmp_path):
    """Create database with schema and tenant/account for FK constraints."""
    proxy = MailProxyBase(db_path=str(tmp_path / "test.db"))
    await proxy.db.connect()
    await proxy.db.check_structure()
    # Create tenant and account for FK constraints
    await proxy.db.table("tenants").insert({"id": "t1", "name": "Test Tenant", "active": 1})
    await proxy.db.table("accounts").add({
        "id": "a1",
        "tenant_id": "t1",
        "host": "smtp.example.com",
        "port": 587,
    })
    yield proxy.db
    await proxy.close()


async def insert_message(db, msg_id, tenant_id="t1", account_id="a1", **kwargs):
    """Helper to insert a message."""
    from tools.uid import get_uuid
    pk = kwargs.pop("pk", get_uuid())
    await db.table("messages").insert({
        "pk": pk,
        "id": msg_id,
        "tenant_id": tenant_id,
        "account_id": account_id,
        "payload": '{"to": "test@example.com"}',
        **kwargs,
    })
    return pk


class TestMessagesTableGet:
    """Tests for MessagesTable.get() method."""

    async def test_get_nonexistent_returns_none(self, db):
        """get() returns None for non-existent message."""
        messages = db.table("messages")
        result = await messages.get("nonexistent", "t1")
        assert result is None

    async def test_get_existing_message(self, db):
        """get() returns message dict."""
        messages = db.table("messages")
        pk = await insert_message(db, "msg1")
        result = await messages.get("msg1", "t1")
        assert result is not None
        assert result["id"] == "msg1"
        assert result["pk"] == pk

    async def test_get_decodes_payload(self, db):
        """get() decodes payload JSON into 'message' field."""
        messages = db.table("messages")
        await insert_message(db, "msg1")
        result = await messages.get("msg1", "t1")
        assert "message" in result
        assert result["message"]["to"] == "test@example.com"

    async def test_get_wrong_tenant_returns_none(self, db):
        """get() returns None for wrong tenant."""
        messages = db.table("messages")
        await insert_message(db, "msg1", tenant_id="t1")
        result = await messages.get("msg1", "wrong_tenant")
        assert result is None


class TestMessagesTableGetByPk:
    """Tests for MessagesTable.get_by_pk() method."""

    async def test_get_by_pk_nonexistent(self, db):
        """get_by_pk() returns None for non-existent pk."""
        messages = db.table("messages")
        result = await messages.get_by_pk("nonexistent-pk")
        assert result is None

    async def test_get_by_pk_existing(self, db):
        """get_by_pk() returns message by internal pk."""
        messages = db.table("messages")
        pk = await insert_message(db, "msg1")
        result = await messages.get_by_pk(pk)
        assert result is not None
        assert result["id"] == "msg1"


class TestMessagesTableRemoveByPk:
    """Tests for MessagesTable.remove_by_pk() method."""

    async def test_remove_by_pk_existing(self, db):
        """remove_by_pk() deletes message and returns True."""
        messages = db.table("messages")
        pk = await insert_message(db, "msg1")
        result = await messages.remove_by_pk(pk)
        assert result is True
        assert await messages.get_by_pk(pk) is None

    async def test_remove_by_pk_nonexistent(self, db):
        """remove_by_pk() returns False for non-existent pk."""
        messages = db.table("messages")
        result = await messages.remove_by_pk("nonexistent-pk")
        assert result is False


class TestMessagesTableInsertBatch:
    """Tests for MessagesTable.insert_batch() method."""

    async def test_insert_batch_single(self, db):
        """insert_batch() inserts a single message."""
        messages = db.table("messages")
        result = await messages.insert_batch([
            {"id": "msg1", "tenant_id": "t1", "account_id": "a1", "payload": {"to": "x@x.com"}}
        ], auto_pec=False)
        assert len(result) == 1
        assert result[0]["id"] == "msg1"
        assert "pk" in result[0]

    async def test_insert_batch_multiple(self, db):
        """insert_batch() inserts multiple messages."""
        messages = db.table("messages")
        result = await messages.insert_batch([
            {"id": "msg1", "tenant_id": "t1", "account_id": "a1", "payload": {"to": "a@x.com"}},
            {"id": "msg2", "tenant_id": "t1", "account_id": "a1", "payload": {"to": "b@x.com"}},
            {"id": "msg3", "tenant_id": "t1", "account_id": "a1", "payload": {"to": "c@x.com"}},
        ], auto_pec=False)
        assert len(result) == 3

    async def test_insert_batch_with_priority(self, db):
        """insert_batch() respects priority field."""
        messages = db.table("messages")
        await messages.insert_batch([
            {"id": "msg1", "tenant_id": "t1", "account_id": "a1", "priority": 0, "payload": {"to": "x@x.com"}}
        ], auto_pec=False)
        msg = await messages.get("msg1", "t1")
        assert msg["priority"] == 0

    async def test_insert_batch_with_deferred(self, db):
        """insert_batch() stores deferred_ts."""
        messages = db.table("messages")
        future_ts = int(time.time()) + 3600
        await messages.insert_batch([
            {"id": "msg1", "tenant_id": "t1", "account_id": "a1", "deferred_ts": future_ts, "payload": {"to": "x@x.com"}}
        ], auto_pec=False)
        msg = await messages.get("msg1", "t1")
        assert msg["deferred_ts"] == future_ts

    async def test_insert_batch_skips_processed(self, db):
        """insert_batch() skips messages already processed (smtp_ts set)."""
        messages = db.table("messages")
        # Insert and mark as sent
        await insert_message(db, "msg1", smtp_ts=12345)
        # Try to update via insert_batch
        result = await messages.insert_batch([
            {"id": "msg1", "tenant_id": "t1", "account_id": "a1", "payload": {"to": "new@x.com"}}
        ], auto_pec=False)
        assert len(result) == 0  # Skipped

    async def test_insert_batch_updates_pending(self, db):
        """insert_batch() updates existing pending message."""
        messages = db.table("messages")
        await insert_message(db, "msg1", priority=2)
        result = await messages.insert_batch([
            {"id": "msg1", "tenant_id": "t1", "account_id": "a1", "priority": 0, "payload": {"to": "x@x.com"}}
        ], auto_pec=False)
        assert len(result) == 1
        msg = await messages.get("msg1", "t1")
        assert msg["priority"] == 0  # Updated


class TestMessagesTableFetchReady:
    """Tests for MessagesTable.fetch_ready() method."""

    async def test_fetch_ready_returns_pending(self, db):
        """fetch_ready() returns pending messages."""
        messages = db.table("messages")
        await insert_message(db, "msg1")
        await insert_message(db, "msg2")
        now_ts = int(time.time())
        result = await messages.fetch_ready(limit=10, now_ts=now_ts)
        assert len(result) == 2

    async def test_fetch_ready_excludes_processed(self, db):
        """fetch_ready() excludes messages with smtp_ts set."""
        messages = db.table("messages")
        await insert_message(db, "msg1")
        await insert_message(db, "msg2", smtp_ts=12345)
        now_ts = int(time.time())
        result = await messages.fetch_ready(limit=10, now_ts=now_ts)
        assert len(result) == 1
        assert result[0]["id"] == "msg1"

    async def test_fetch_ready_excludes_deferred(self, db):
        """fetch_ready() excludes messages deferred to future."""
        messages = db.table("messages")
        now_ts = int(time.time())
        await insert_message(db, "msg1")
        await insert_message(db, "msg2", deferred_ts=now_ts + 3600)
        result = await messages.fetch_ready(limit=10, now_ts=now_ts)
        assert len(result) == 1
        assert result[0]["id"] == "msg1"

    async def test_fetch_ready_includes_past_deferred(self, db):
        """fetch_ready() includes messages with past deferred_ts."""
        messages = db.table("messages")
        now_ts = int(time.time())
        await insert_message(db, "msg1", deferred_ts=now_ts - 100)
        result = await messages.fetch_ready(limit=10, now_ts=now_ts)
        assert len(result) == 1

    async def test_fetch_ready_respects_limit(self, db):
        """fetch_ready() respects limit parameter."""
        messages = db.table("messages")
        for i in range(5):
            await insert_message(db, f"msg{i}")
        now_ts = int(time.time())
        result = await messages.fetch_ready(limit=2, now_ts=now_ts)
        assert len(result) == 2

    async def test_fetch_ready_orders_by_priority(self, db):
        """fetch_ready() orders by priority ASC."""
        messages = db.table("messages")
        await insert_message(db, "low", priority=3)
        await insert_message(db, "high", priority=0)
        await insert_message(db, "medium", priority=2)
        now_ts = int(time.time())
        result = await messages.fetch_ready(limit=10, now_ts=now_ts)
        ids = [m["id"] for m in result]
        assert ids == ["high", "medium", "low"]

    async def test_fetch_ready_filter_by_priority(self, db):
        """fetch_ready() can filter by exact priority."""
        messages = db.table("messages")
        await insert_message(db, "p0", priority=0)
        await insert_message(db, "p2", priority=2)
        now_ts = int(time.time())
        result = await messages.fetch_ready(limit=10, now_ts=now_ts, priority=0)
        assert len(result) == 1
        assert result[0]["id"] == "p0"


class TestMessagesTableMarkSent:
    """Tests for MessagesTable.mark_sent() method."""

    async def test_mark_sent_sets_smtp_ts(self, db):
        """mark_sent() sets smtp_ts."""
        messages = db.table("messages")
        pk = await insert_message(db, "msg1")
        ts = int(time.time())
        await messages.mark_sent(pk, ts)
        msg = await messages.get_by_pk(pk)
        assert msg["smtp_ts"] == ts

    async def test_mark_sent_clears_deferred(self, db):
        """mark_sent() clears deferred_ts."""
        messages = db.table("messages")
        pk = await insert_message(db, "msg1", deferred_ts=99999)
        ts = int(time.time())
        await messages.mark_sent(pk, ts)
        msg = await messages.get_by_pk(pk)
        assert msg["deferred_ts"] is None


class TestMessagesTableMarkError:
    """Tests for MessagesTable.mark_error() method."""

    async def test_mark_error_sets_smtp_ts(self, db):
        """mark_error() sets smtp_ts."""
        messages = db.table("messages")
        pk = await insert_message(db, "msg1")
        ts = int(time.time())
        await messages.mark_error(pk, ts)
        msg = await messages.get_by_pk(pk)
        assert msg["smtp_ts"] == ts


class TestMessagesTableDeferred:
    """Tests for set_deferred() and clear_deferred() methods."""

    async def test_set_deferred(self, db):
        """set_deferred() sets deferred_ts and clears smtp_ts."""
        messages = db.table("messages")
        pk = await insert_message(db, "msg1", smtp_ts=12345)
        future_ts = int(time.time()) + 3600
        await messages.set_deferred(pk, future_ts)
        msg = await messages.get_by_pk(pk)
        assert msg["deferred_ts"] == future_ts
        assert msg["smtp_ts"] is None

    async def test_clear_deferred(self, db):
        """clear_deferred() clears deferred_ts."""
        messages = db.table("messages")
        pk = await insert_message(db, "msg1", deferred_ts=99999)
        await messages.clear_deferred(pk)
        msg = await messages.get_by_pk(pk)
        assert msg["deferred_ts"] is None


class TestMessagesTableListAll:
    """Tests for MessagesTable.list_all() method."""

    async def test_list_all_empty(self, db):
        """list_all() returns empty list when no messages."""
        messages = db.table("messages")
        result = await messages.list_all()
        assert result == []

    async def test_list_all_returns_all(self, db):
        """list_all() returns all messages."""
        messages = db.table("messages")
        await insert_message(db, "msg1")
        await insert_message(db, "msg2")
        result = await messages.list_all()
        assert len(result) == 2

    async def test_list_all_by_tenant(self, db):
        """list_all() filters by tenant_id."""
        messages = db.table("messages")
        await insert_message(db, "msg1", tenant_id="t1")
        # Create second tenant
        await db.table("tenants").insert({"id": "t2", "name": "Tenant 2", "active": 1})
        await db.table("accounts").add({"id": "a2", "tenant_id": "t2", "host": "h", "port": 25})
        await insert_message(db, "msg2", tenant_id="t2", account_id="a2")
        result = await messages.list_all(tenant_id="t1")
        assert len(result) == 1
        assert result[0]["id"] == "msg1"

    async def test_list_all_active_only(self, db):
        """list_all(active_only=True) excludes processed."""
        messages = db.table("messages")
        await insert_message(db, "pending")
        await insert_message(db, "sent", smtp_ts=12345)
        result = await messages.list_all(active_only=True)
        assert len(result) == 1
        assert result[0]["id"] == "pending"


class TestMessagesTableCountActive:
    """Tests for MessagesTable.count_active() method."""

    async def test_count_active_empty(self, db):
        """count_active() returns 0 when no messages."""
        messages = db.table("messages")
        count = await messages.count_active()
        assert count == 0

    async def test_count_active_excludes_processed(self, db):
        """count_active() excludes messages with smtp_ts."""
        messages = db.table("messages")
        await insert_message(db, "pending1")
        await insert_message(db, "pending2")
        await insert_message(db, "sent", smtp_ts=12345)
        count = await messages.count_active()
        assert count == 2


class TestMessagesTablePurgeForAccount:
    """Tests for MessagesTable.purge_for_account() method."""

    async def test_purge_for_account(self, db):
        """purge_for_account() deletes messages for account."""
        messages = db.table("messages")
        await insert_message(db, "msg1", account_id="a1")
        await insert_message(db, "msg2", account_id="a1")
        await messages.purge_for_account("a1")
        count = await messages.count_active()
        assert count == 0


class TestMessagesTableExistingIds:
    """Tests for MessagesTable.existing_ids() method."""

    async def test_existing_ids(self, db):
        """existing_ids() returns set of existing message IDs."""
        messages = db.table("messages")
        await insert_message(db, "msg1")
        await insert_message(db, "msg2")
        result = await messages.existing_ids(["msg1", "msg2", "msg3"])
        assert result == {"msg1", "msg2"}

    async def test_existing_ids_empty(self, db):
        """existing_ids() returns empty set when none exist."""
        messages = db.table("messages")
        result = await messages.existing_ids(["msg1", "msg2"])
        assert result == set()
