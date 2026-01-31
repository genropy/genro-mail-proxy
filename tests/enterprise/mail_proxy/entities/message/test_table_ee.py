# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: BSL-1.1
"""Tests for MessagesTable EE methods (PEC)."""

import time

import pytest

from core.mail_proxy.proxy_base import MailProxyBase
from core.mail_proxy.proxy_config import ProxyConfig


@pytest.fixture
async def db(tmp_path):
    """Create database with schema and tenant/account for FK constraints."""
    proxy = MailProxyBase(ProxyConfig(db_path=str(tmp_path / "test.db")))
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
    from genro_toolbox import get_uuid
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


class TestMessagesTablePEC:
    """Tests for MessagesTable PEC methods (EE)."""

    async def test_clear_pec_flag(self, db):
        """clear_pec_flag() sets is_pec to 0."""
        messages = db.table("messages")
        pk = await insert_message(db, "msg1", is_pec=1)
        await messages.clear_pec_flag(pk)
        msg = await messages.get_by_pk(pk)
        assert msg["is_pec"] is False

    async def test_get_pec_without_acceptance(self, db):
        """get_pec_without_acceptance() finds PEC messages without receipt."""
        messages = db.table("messages")
        now_ts = int(time.time())
        # PEC message sent in the past
        pk = await insert_message(db, "pec_msg", is_pec=1, smtp_ts=now_ts - 3600)
        # Non-PEC message
        await insert_message(db, "normal_msg", is_pec=0, smtp_ts=now_ts - 3600)

        result = await messages.get_pec_without_acceptance(now_ts)
        assert len(result) == 1
        assert result[0]["id"] == "pec_msg"

    async def test_get_pec_without_acceptance_excludes_recent(self, db):
        """get_pec_without_acceptance() excludes messages sent after cutoff."""
        messages = db.table("messages")
        now_ts = int(time.time())
        # PEC message sent recently (after cutoff)
        await insert_message(db, "recent_pec", is_pec=1, smtp_ts=now_ts + 100)

        result = await messages.get_pec_without_acceptance(now_ts)
        assert len(result) == 0
