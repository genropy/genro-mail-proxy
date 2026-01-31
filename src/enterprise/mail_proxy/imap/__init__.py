# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: BSL-1.1
"""Async IMAP client for bounce and PEC receipt polling.

This package provides an async IMAP client wrapper built on aioimaplib,
used by BounceReceiver and PecReceiver for mailbox polling.

Components:
    IMAPClient: Async IMAP client with connect, fetch, and delete operations.
    IMAPMessage: Dataclass representing a fetched message (uid, raw bytes).

Example:
    Fetch unread messages from a mailbox::

        from enterprise.mail_proxy.imap import IMAPClient

        client = IMAPClient()
        await client.connect(
            host="imap.example.com",
            port=993,
            user="bounces@example.com",
            password="secret",
        )
        await client.select_folder("INBOX")
        messages = await client.fetch_unseen()
        for msg in messages:
            print(f"UID {msg.uid}: {len(msg.raw)} bytes")
        await client.disconnect()

Note:
    IMAPClient handles SSL/TLS connections, UIDVALIDITY tracking, and
    graceful error recovery for long-running polling operations.
"""

from .client import IMAPClient

__all__ = ["IMAPClient"]
