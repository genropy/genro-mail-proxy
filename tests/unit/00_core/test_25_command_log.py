# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Tests for the command_log audit trail functionality."""

import time

import pytest

from mail_proxy.mailproxy_db import MailProxyDb


@pytest.mark.asyncio
async def test_command_log_basic_crud(tmp_path):
    """Test basic command logging and retrieval."""
    db = tmp_path / "test.db"
    p = MailProxyDb(str(db))
    await p.init_db()

    # Log a command
    cmd_id = await p.log_command(
        endpoint="POST /commands/add-messages",
        payload={"messages": [{"id": "msg1", "from": "a@example.com"}]},
        tenant_id="acme",
        response_status=200,
    )
    assert cmd_id > 0

    # List commands
    commands = await p.list_commands()
    assert len(commands) == 1
    assert commands[0]["endpoint"] == "POST /commands/add-messages"
    assert commands[0]["tenant_id"] == "acme"
    assert commands[0]["response_status"] == 200
    assert commands[0]["payload"]["messages"][0]["id"] == "msg1"


@pytest.mark.asyncio
async def test_command_log_filters(tmp_path):
    """Test command log filtering by tenant, endpoint, and time range."""
    db = tmp_path / "test.db"
    p = MailProxyDb(str(db))
    await p.init_db()

    now = int(time.time())

    # Log commands for different tenants and endpoints
    await p.command_log.log_command(
        endpoint="POST /commands/add-messages",
        payload={"messages": []},
        tenant_id="acme",
        command_ts=now - 100,
    )
    await p.command_log.log_command(
        endpoint="POST /tenant",
        payload={"id": "beta"},
        tenant_id=None,
        command_ts=now - 50,
    )
    await p.command_log.log_command(
        endpoint="DELETE /account/smtp1",
        payload={},
        tenant_id="beta",
        command_ts=now,
    )

    # Filter by tenant
    acme_cmds = await p.list_commands(tenant_id="acme")
    assert len(acme_cmds) == 1
    assert acme_cmds[0]["endpoint"] == "POST /commands/add-messages"

    # Filter by endpoint
    tenant_cmds = await p.list_commands(endpoint_filter="tenant")
    assert len(tenant_cmds) == 1
    assert tenant_cmds[0]["endpoint"] == "POST /tenant"

    # Filter by time range
    recent_cmds = await p.list_commands(since_ts=now - 60)
    assert len(recent_cmds) == 2  # POST /tenant and DELETE /account

    old_cmds = await p.list_commands(until_ts=now - 60)
    assert len(old_cmds) == 1  # Only POST /commands/add-messages


@pytest.mark.asyncio
async def test_command_log_export(tmp_path):
    """Test export in replay-friendly format."""
    db = tmp_path / "test.db"
    p = MailProxyDb(str(db))
    await p.init_db()

    now = int(time.time())

    await p.command_log.log_command(
        endpoint="POST /commands/add-messages",
        payload={"messages": [{"id": "msg1"}]},
        tenant_id="acme",
        response_status=200,
        response_body={"ok": True, "queued": 1},
        command_ts=now,
    )

    # Export only includes fields needed for replay
    exported = await p.export_commands()
    assert len(exported) == 1
    assert "endpoint" in exported[0]
    assert "tenant_id" in exported[0]
    assert "payload" in exported[0]
    assert "command_ts" in exported[0]
    # response_status and response_body should NOT be in export
    assert "response_status" not in exported[0]
    assert "response_body" not in exported[0]


@pytest.mark.asyncio
async def test_command_log_purge(tmp_path):
    """Test purging old command logs."""
    db = tmp_path / "test.db"
    p = MailProxyDb(str(db))
    await p.init_db()

    now = int(time.time())

    # Log commands at different times
    await p.command_log.log_command(
        endpoint="POST /commands/add-messages",
        payload={},
        command_ts=now - 1000,  # Old
    )
    await p.command_log.log_command(
        endpoint="POST /commands/add-messages",
        payload={},
        command_ts=now - 500,  # Medium
    )
    await p.command_log.log_command(
        endpoint="POST /commands/add-messages",
        payload={},
        command_ts=now,  # Recent
    )

    # Purge old commands
    deleted = await p.purge_commands_before(now - 600)
    assert deleted == 1

    # Verify remaining
    remaining = await p.list_commands()
    assert len(remaining) == 2


@pytest.mark.asyncio
async def test_command_log_table_direct(tmp_path):
    """Test CommandLogTable directly."""
    db = tmp_path / "test.db"
    p = MailProxyDb(str(db))
    await p.init_db()

    # Test get_command
    cmd_id = await p.command_log.log_command(
        endpoint="POST /account",
        payload={"id": "smtp1", "host": "smtp.example.com"},
        tenant_id="acme",
        response_status=200,
        response_body={"ok": True},
    )

    cmd = await p.command_log.get_command(cmd_id)
    assert cmd is not None
    assert cmd["endpoint"] == "POST /account"
    assert cmd["payload"]["id"] == "smtp1"
    assert cmd["response_body"]["ok"] is True

    # Test get_command with non-existent ID
    cmd = await p.command_log.get_command(99999)
    assert cmd is None
