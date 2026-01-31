# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Message event entity module for delivery tracking.

This module provides the MessageEventTable for recording and querying
message lifecycle events (sent, error, deferred, bounce, PEC receipts).

Components:
    MessageEventTable: Database table manager for event storage.

Example:
    Track message delivery::

        from core.mail_proxy.proxy_base import MailProxyBase

        proxy = MailProxyBase(db_path=":memory:")
        await proxy.init()

        events = proxy.db.table("message_events")

        # Record sent event
        await events.add_event(
            message_pk="550e8400-e29b-41d4-a716-446655440000",
            event_type="sent",
            event_ts=1704067200,
        )

        # Get unreported for sync
        unreported = await events.fetch_unreported(limit=100)

Note:
    Events automatically update message status via trigger_on_inserted.
    This table is internal and has no REST endpoint - events are
    managed through the messages endpoint and reporting system.
"""

from .table import MessageEventTable

__all__ = ["MessageEventTable"]
