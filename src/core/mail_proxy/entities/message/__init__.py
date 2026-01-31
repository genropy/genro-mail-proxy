# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Email message entity module.

This module provides the Message entity for managing email messages
in the delivery queue. Messages contain email content (from, to,
subject, body, attachments) and progress through delivery states.

Components:
    MessagesTable: Database table manager for message queue.
    MessageEndpoint: REST API endpoint for CRUD operations.
    MessageStatus: Enum for message delivery status.
    FetchMode: Enum for attachment fetch modes.
    AttachmentPayload: Pydantic model for attachment specification.

Message Lifecycle:
    1. PENDING: Queued, waiting for delivery
    2. DEFERRED: Temporarily delayed (rate limit or retry)
    3. SENT: Successfully delivered to SMTP server
    4. ERROR: Delivery failed with permanent error

Example:
    Queue and track messages::

        from core.mail_proxy.proxy_base import MailProxyBase

        proxy = MailProxyBase(db_path=":memory:")
        await proxy.init()

        # Queue via table
        messages = proxy.db.table("messages")
        await messages.insert_batch([{
            "id": "msg-001",
            "tenant_id": "acme",
            "account_id": "main",
            "payload": {
                "from": "sender@acme.com",
                "to": ["user@example.com"],
                "subject": "Hello",
                "body": "Test message",
            },
        }])

        # Or via endpoint
        from core.mail_proxy.entities.message import MessageEndpoint
        endpoint = MessageEndpoint(messages)
        await endpoint.add(
            id="msg-002",
            tenant_id="acme",
            account_id="main",
            from_addr="sender@acme.com",
            to=["user@example.com"],
            subject="Hello",
            body="Test",
        )

Note:
    Enterprise Edition (EE) extends both classes with PEC support
    for Italian certified email via MessagesTable_EE and MessageEndpoint_EE.
"""

from .endpoint import (
    AttachmentPayload,
    FetchMode,
    MessageEndpoint,
    MessageStatus,
)
from .table import MessagesTable

__all__ = [
    "AttachmentPayload",
    "FetchMode",
    "MessageEndpoint",
    "MessagesTable",
    "MessageStatus",
]
