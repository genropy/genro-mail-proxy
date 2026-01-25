# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Tests for bounce detection integration with delivery reports."""

import pytest

from mail_proxy.mailproxy_db import MailProxyDb


@pytest.mark.asyncio
async def test_fetch_reports_includes_bounce_fields(tmp_path):
    """Test that fetch_reports includes bounce fields when present."""
    db = MailProxyDb(str(tmp_path / "test.db"))
    await db.init_db()

    # Create account
    await db.add_account({
        "id": "acc1",
        "host": "smtp.example.com",
        "port": 587,
    })

    # Insert message
    await db.insert_messages([{
        "id": "msg1",
        "account_id": "acc1",
        "payload": {"from": "a@b.com", "to": ["c@d.com"], "subject": "Test", "body": "Hi"},
    }])

    # Mark as sent
    sent_ts = 1700000000
    await db.mark_sent("msg1", sent_ts)

    # Fetch reports - no bounce yet
    reports = await db.fetch_reports(limit=10)
    assert len(reports) == 1
    assert reports[0]["id"] == "msg1"
    assert reports[0].get("bounce_type") is None
    assert reports[0].get("bounce_ts") is None


@pytest.mark.asyncio
async def test_fetch_reports_includes_bounced_messages(tmp_path):
    """Test that fetch_reports returns messages with bounce even if already reported."""
    db = MailProxyDb(str(tmp_path / "test.db"))
    await db.init_db()

    # Create account and message
    await db.add_account({"id": "acc1", "host": "smtp.example.com", "port": 587})
    await db.insert_messages([{
        "id": "msg1",
        "account_id": "acc1",
        "payload": {"from": "a@b.com", "to": ["c@d.com"], "subject": "Test", "body": "Hi"},
    }])

    # Mark as sent and reported
    sent_ts = 1700000000
    await db.mark_sent("msg1", sent_ts)
    await db.mark_reported(["msg1"], sent_ts + 10)

    # Verify message is not in reports anymore (already reported)
    reports = await db.fetch_reports(limit=10)
    assert len(reports) == 0

    # Now simulate a bounce detection - update message with bounce info
    await db.adapter.execute(
        """
        UPDATE messages SET
            bounce_type = :bounce_type,
            bounce_code = :bounce_code,
            bounce_reason = :bounce_reason,
            bounce_ts = CURRENT_TIMESTAMP
        WHERE id = :id
        """,
        {
            "id": "msg1",
            "bounce_type": "hard",
            "bounce_code": "550",
            "bounce_reason": "User unknown",
        },
    )

    # Message should appear again because bounce_reported_ts is NULL
    reports = await db.fetch_reports(limit=10)
    assert len(reports) == 1
    assert reports[0]["id"] == "msg1"
    assert reports[0]["bounce_type"] == "hard"
    assert reports[0]["bounce_code"] == "550"
    assert reports[0]["bounce_reason"] == "User unknown"
    assert reports[0]["bounce_ts"] is not None
    assert reports[0]["bounce_reported_ts"] is None


@pytest.mark.asyncio
async def test_mark_reported_with_bounce_ids(tmp_path):
    """Test that mark_reported can separately track bounce reporting."""
    db = MailProxyDb(str(tmp_path / "test.db"))
    await db.init_db()

    # Create account and messages
    await db.add_account({"id": "acc1", "host": "smtp.example.com", "port": 587})
    await db.insert_messages([
        {
            "id": "msg1",
            "account_id": "acc1",
            "payload": {"from": "a@b.com", "to": ["c@d.com"], "subject": "Test1", "body": "Hi"},
        },
        {
            "id": "msg2",
            "account_id": "acc1",
            "payload": {"from": "a@b.com", "to": ["e@f.com"], "subject": "Test2", "body": "Hi"},
        },
    ])

    # Mark both as sent
    sent_ts = 1700000000
    await db.mark_sent("msg1", sent_ts)
    await db.mark_sent("msg2", sent_ts)

    # Report msg1 as sent, msg2 has bounce
    await db.adapter.execute(
        """
        UPDATE messages SET
            bounce_type = 'hard',
            bounce_code = '550',
            bounce_reason = 'User unknown',
            bounce_ts = CURRENT_TIMESTAMP
        WHERE id = 'msg2'
        """,
        {},
    )

    # Mark reported - msg1 is new report, msg2 has bounce
    reported_ts = sent_ts + 100
    await db.mark_reported(["msg1"], reported_ts)  # new delivery reports
    await db.mark_bounce_reported(["msg2"], reported_ts)  # bounce notifications

    # Check that msg1 has reported_ts set
    msg1 = await db.get_message("msg1")
    assert msg1["reported_ts"] == reported_ts

    # Check that msg2 has bounce_reported_ts set but NOT reported_ts
    # (it wasn't in the main message_ids list)
    msg2 = await db.get_message("msg2")
    assert msg2["bounce_reported_ts"] == reported_ts


@pytest.mark.asyncio
async def test_bounce_not_in_reports_after_bounce_reported(tmp_path):
    """Test that bounce messages disappear from reports after bounce_reported_ts is set."""
    db = MailProxyDb(str(tmp_path / "test.db"))
    await db.init_db()

    # Setup
    await db.add_account({"id": "acc1", "host": "smtp.example.com", "port": 587})
    await db.insert_messages([{
        "id": "msg1",
        "account_id": "acc1",
        "payload": {"from": "a@b.com", "to": ["c@d.com"], "subject": "Test", "body": "Hi"},
    }])

    # Mark as sent, reported, then bounced
    sent_ts = 1700000000
    await db.mark_sent("msg1", sent_ts)
    await db.mark_reported(["msg1"], sent_ts + 10)

    # Add bounce
    await db.adapter.execute(
        """
        UPDATE messages SET
            bounce_type = 'soft',
            bounce_code = '421',
            bounce_reason = 'Try again later',
            bounce_ts = CURRENT_TIMESTAMP
        WHERE id = 'msg1'
        """,
        {},
    )

    # Should be in reports (bounce not yet reported)
    reports = await db.fetch_reports(limit=10)
    assert len(reports) == 1

    # Mark bounce as reported
    await db.mark_bounce_reported(["msg1"], sent_ts + 20)

    # Should NOT be in reports anymore
    reports = await db.fetch_reports(limit=10)
    assert len(reports) == 0


@pytest.mark.asyncio
async def test_fetch_reports_both_new_and_bounce(tmp_path):
    """Test fetch_reports returns both new messages and bounce updates."""
    db = MailProxyDb(str(tmp_path / "test.db"))
    await db.init_db()

    # Setup
    await db.add_account({"id": "acc1", "host": "smtp.example.com", "port": 587})
    await db.insert_messages([
        {
            "id": "msg-new",
            "account_id": "acc1",
            "payload": {"from": "a@b.com", "to": ["c@d.com"], "subject": "New", "body": "Hi"},
        },
        {
            "id": "msg-bounced",
            "account_id": "acc1",
            "payload": {"from": "a@b.com", "to": ["e@f.com"], "subject": "Bounced", "body": "Hi"},
        },
    ])

    sent_ts = 1700000000

    # msg-new: just sent, not reported
    await db.mark_sent("msg-new", sent_ts)

    # msg-bounced: sent, reported, then bounced
    await db.mark_sent("msg-bounced", sent_ts)
    await db.mark_reported(["msg-bounced"], sent_ts + 10)
    await db.adapter.execute(
        """
        UPDATE messages SET
            bounce_type = 'hard',
            bounce_code = '550',
            bounce_reason = 'User unknown',
            bounce_ts = CURRENT_TIMESTAMP
        WHERE id = 'msg-bounced'
        """,
        {},
    )

    # Both should be in reports
    reports = await db.fetch_reports(limit=10)
    assert len(reports) == 2

    ids = {r["id"] for r in reports}
    assert ids == {"msg-new", "msg-bounced"}

    # Find the bounced one and verify fields
    bounced = next(r for r in reports if r["id"] == "msg-bounced")
    assert bounced["bounce_type"] == "hard"
    assert bounced["bounce_code"] == "550"
