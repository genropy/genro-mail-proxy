# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: BSL-1.1
"""Tests for CommandLogTable - API audit trail (EE)."""

import time

import pytest

from core.mail_proxy.proxy_base import MailProxyBase
from core.mail_proxy.proxy_config import ProxyConfig


@pytest.fixture
async def db(tmp_path):
    """Create database with schema."""
    proxy = MailProxyBase(ProxyConfig(db_path=str(tmp_path / "test.db")))
    await proxy.db.connect()
    await proxy.db.check_structure()
    yield proxy.db
    await proxy.close()


class TestCommandLogTableLogCommand:
    """Tests for CommandLogTable.log_command() method."""

    async def test_log_command_basic(self, db):
        """log_command() creates entry and returns ID."""
        cmd_log = db.table("command_log")

        cmd_id = await cmd_log.log_command(
            endpoint="POST /commands/add-messages",
            payload={"messages": [{"id": "m1"}]},
        )

        assert cmd_id > 0

    async def test_log_command_with_tenant(self, db):
        """log_command() stores tenant_id."""
        cmd_log = db.table("command_log")

        cmd_id = await cmd_log.log_command(
            endpoint="POST /commands/add-messages",
            payload={"messages": []},
            tenant_id="t1",
        )

        cmd = await cmd_log.get_command(cmd_id)
        assert cmd["tenant_id"] == "t1"

    async def test_log_command_with_response(self, db):
        """log_command() stores response status and body."""
        cmd_log = db.table("command_log")

        cmd_id = await cmd_log.log_command(
            endpoint="POST /commands/add-messages",
            payload={"messages": []},
            response_status=200,
            response_body={"added": 5, "skipped": 0},
        )

        cmd = await cmd_log.get_command(cmd_id)
        assert cmd["response_status"] == 200
        assert cmd["response_body"] == {"added": 5, "skipped": 0}

    async def test_log_command_with_custom_ts(self, db):
        """log_command() uses provided command_ts."""
        cmd_log = db.table("command_log")
        custom_ts = 1700000000

        cmd_id = await cmd_log.log_command(
            endpoint="POST /account",
            payload={"id": "smtp1"},
            command_ts=custom_ts,
        )

        cmd = await cmd_log.get_command(cmd_id)
        assert cmd["command_ts"] == custom_ts

    async def test_log_command_auto_ts(self, db):
        """log_command() uses current time if no command_ts."""
        cmd_log = db.table("command_log")
        before = int(time.time())

        cmd_id = await cmd_log.log_command(
            endpoint="DELETE /account/smtp1",
            payload={},
        )

        after = int(time.time())
        cmd = await cmd_log.get_command(cmd_id)
        assert before <= cmd["command_ts"] <= after


class TestCommandLogTableGetCommand:
    """Tests for CommandLogTable.get_command() method."""

    async def test_get_command_existing(self, db):
        """get_command() returns command dict."""
        cmd_log = db.table("command_log")

        cmd_id = await cmd_log.log_command(
            endpoint="POST /tenant",
            payload={"id": "t1", "name": "Test"},
            tenant_id="t1",
        )

        cmd = await cmd_log.get_command(cmd_id)
        assert cmd is not None
        assert cmd["id"] == cmd_id
        assert cmd["endpoint"] == "POST /tenant"
        assert cmd["payload"] == {"id": "t1", "name": "Test"}

    async def test_get_command_nonexistent(self, db):
        """get_command() returns None for nonexistent ID."""
        cmd_log = db.table("command_log")

        cmd = await cmd_log.get_command(99999)
        assert cmd is None

    async def test_get_command_decodes_json(self, db):
        """get_command() decodes JSON payload and response_body."""
        cmd_log = db.table("command_log")

        cmd_id = await cmd_log.log_command(
            endpoint="POST /commands/add-messages",
            payload={"nested": {"data": [1, 2, 3]}},
            response_body={"result": "ok"},
        )

        cmd = await cmd_log.get_command(cmd_id)
        assert cmd["payload"] == {"nested": {"data": [1, 2, 3]}}
        assert cmd["response_body"] == {"result": "ok"}


class TestCommandLogTableListCommands:
    """Tests for CommandLogTable.list_commands() method."""

    async def test_list_commands_empty(self, db):
        """list_commands() returns empty list when no commands."""
        cmd_log = db.table("command_log")

        result = await cmd_log.list_commands()
        assert result == []

    async def test_list_commands_all(self, db):
        """list_commands() returns all commands."""
        cmd_log = db.table("command_log")

        await cmd_log.log_command(endpoint="POST /a", payload={})
        await cmd_log.log_command(endpoint="POST /b", payload={})
        await cmd_log.log_command(endpoint="POST /c", payload={})

        result = await cmd_log.list_commands()
        assert len(result) == 3

    async def test_list_commands_filter_by_tenant(self, db):
        """list_commands() filters by tenant_id."""
        cmd_log = db.table("command_log")

        await cmd_log.log_command(endpoint="POST /a", payload={}, tenant_id="t1")
        await cmd_log.log_command(endpoint="POST /b", payload={}, tenant_id="t2")
        await cmd_log.log_command(endpoint="POST /c", payload={}, tenant_id="t1")

        result = await cmd_log.list_commands(tenant_id="t1")
        assert len(result) == 2
        assert all(c["tenant_id"] == "t1" for c in result)

    async def test_list_commands_filter_by_since_ts(self, db):
        """list_commands() filters by since_ts."""
        cmd_log = db.table("command_log")
        base_ts = 1700000000

        await cmd_log.log_command(endpoint="POST /a", payload={}, command_ts=base_ts)
        await cmd_log.log_command(endpoint="POST /b", payload={}, command_ts=base_ts + 100)
        await cmd_log.log_command(endpoint="POST /c", payload={}, command_ts=base_ts + 200)

        result = await cmd_log.list_commands(since_ts=base_ts + 50)
        assert len(result) == 2

    async def test_list_commands_filter_by_until_ts(self, db):
        """list_commands() filters by until_ts."""
        cmd_log = db.table("command_log")
        base_ts = 1700000000

        await cmd_log.log_command(endpoint="POST /a", payload={}, command_ts=base_ts)
        await cmd_log.log_command(endpoint="POST /b", payload={}, command_ts=base_ts + 100)
        await cmd_log.log_command(endpoint="POST /c", payload={}, command_ts=base_ts + 200)

        result = await cmd_log.list_commands(until_ts=base_ts + 150)
        assert len(result) == 2

    async def test_list_commands_filter_by_endpoint(self, db):
        """list_commands() filters by endpoint pattern."""
        cmd_log = db.table("command_log")

        await cmd_log.log_command(endpoint="POST /commands/add-messages", payload={})
        await cmd_log.log_command(endpoint="POST /commands/delete-messages", payload={})
        await cmd_log.log_command(endpoint="POST /account", payload={})

        result = await cmd_log.list_commands(endpoint_filter="/commands/")
        assert len(result) == 2

    async def test_list_commands_limit(self, db):
        """list_commands() respects limit."""
        cmd_log = db.table("command_log")

        for i in range(10):
            await cmd_log.log_command(endpoint=f"POST /cmd{i}", payload={})

        result = await cmd_log.list_commands(limit=5)
        assert len(result) == 5

    async def test_list_commands_offset(self, db):
        """list_commands() respects offset."""
        cmd_log = db.table("command_log")
        base_ts = 1700000000

        for i in range(5):
            await cmd_log.log_command(endpoint=f"POST /cmd{i}", payload={}, command_ts=base_ts + i)

        result = await cmd_log.list_commands(offset=2, limit=10)
        assert len(result) == 3
        assert result[0]["endpoint"] == "POST /cmd2"

    async def test_list_commands_ordered_by_ts(self, db):
        """list_commands() returns commands ordered by timestamp."""
        cmd_log = db.table("command_log")
        base_ts = 1700000000

        await cmd_log.log_command(endpoint="POST /c", payload={}, command_ts=base_ts + 200)
        await cmd_log.log_command(endpoint="POST /a", payload={}, command_ts=base_ts)
        await cmd_log.log_command(endpoint="POST /b", payload={}, command_ts=base_ts + 100)

        result = await cmd_log.list_commands()
        endpoints = [c["endpoint"] for c in result]
        assert endpoints == ["POST /a", "POST /b", "POST /c"]


class TestCommandLogTableExportCommands:
    """Tests for CommandLogTable.export_commands() method."""

    async def test_export_commands_format(self, db):
        """export_commands() returns replay-friendly format."""
        cmd_log = db.table("command_log")
        ts = 1700000000

        await cmd_log.log_command(
            endpoint="POST /account",
            payload={"id": "smtp1", "host": "mail.example.com"},
            tenant_id="t1",
            response_status=200,
            command_ts=ts,
        )

        result = await cmd_log.export_commands()
        assert len(result) == 1
        export = result[0]
        # Only essential fields
        assert set(export.keys()) == {"endpoint", "tenant_id", "payload", "command_ts"}
        assert export["endpoint"] == "POST /account"
        assert export["tenant_id"] == "t1"
        assert export["payload"] == {"id": "smtp1", "host": "mail.example.com"}
        assert export["command_ts"] == ts

    async def test_export_commands_filter_by_tenant(self, db):
        """export_commands() filters by tenant_id."""
        cmd_log = db.table("command_log")

        await cmd_log.log_command(endpoint="POST /a", payload={}, tenant_id="t1")
        await cmd_log.log_command(endpoint="POST /b", payload={}, tenant_id="t2")

        result = await cmd_log.export_commands(tenant_id="t1")
        assert len(result) == 1
        assert result[0]["tenant_id"] == "t1"

    async def test_export_commands_filter_by_time_range(self, db):
        """export_commands() filters by time range."""
        cmd_log = db.table("command_log")
        base_ts = 1700000000

        await cmd_log.log_command(endpoint="POST /a", payload={}, command_ts=base_ts)
        await cmd_log.log_command(endpoint="POST /b", payload={}, command_ts=base_ts + 100)
        await cmd_log.log_command(endpoint="POST /c", payload={}, command_ts=base_ts + 200)

        result = await cmd_log.export_commands(since_ts=base_ts + 50, until_ts=base_ts + 150)
        assert len(result) == 1
        assert result[0]["endpoint"] == "POST /b"


class TestCommandLogTablePurgeBefore:
    """Tests for CommandLogTable.purge_before() method."""

    async def test_purge_before_removes_old(self, db):
        """purge_before() removes commands older than threshold."""
        cmd_log = db.table("command_log")
        base_ts = 1700000000

        await cmd_log.log_command(endpoint="POST /old1", payload={}, command_ts=base_ts)
        await cmd_log.log_command(endpoint="POST /old2", payload={}, command_ts=base_ts + 10)
        await cmd_log.log_command(endpoint="POST /new", payload={}, command_ts=base_ts + 100)

        deleted = await cmd_log.purge_before(base_ts + 50)
        assert deleted == 2

        remaining = await cmd_log.list_commands()
        assert len(remaining) == 1
        assert remaining[0]["endpoint"] == "POST /new"

    async def test_purge_before_returns_count(self, db):
        """purge_before() returns number of deleted records."""
        cmd_log = db.table("command_log")
        base_ts = 1700000000

        for i in range(5):
            await cmd_log.log_command(endpoint=f"POST /cmd{i}", payload={}, command_ts=base_ts + i)

        deleted = await cmd_log.purge_before(base_ts + 3)
        assert deleted == 3

    async def test_purge_before_nothing_to_delete(self, db):
        """purge_before() returns 0 when nothing to delete."""
        cmd_log = db.table("command_log")
        ts = 1700000000

        await cmd_log.log_command(endpoint="POST /new", payload={}, command_ts=ts)

        deleted = await cmd_log.purge_before(ts - 100)
        assert deleted == 0

    async def test_purge_before_empty_table(self, db):
        """purge_before() returns 0 for empty table."""
        cmd_log = db.table("command_log")

        deleted = await cmd_log.purge_before(int(time.time()))
        assert deleted == 0
