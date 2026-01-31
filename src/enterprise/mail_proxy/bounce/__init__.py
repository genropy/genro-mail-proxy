# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: BSL-1.1
"""Bounce detection for email delivery failures.

This package provides IMAP-based bounce detection by polling a mailbox
for bounce notifications and correlating them with sent messages.

Components:
    BounceReceiver: Background task polling IMAP for bounce messages.
    BounceParser: Extracts failure info from bounce notification emails.
    BounceConfig: Configuration dataclass for IMAP connection.
    BounceInfo: Parsed bounce data (recipient, code, diagnostic).

Example:
    Configure and start bounce detection::

        from enterprise.mail_proxy.bounce import BounceConfig

        proxy.configure_bounce_receiver(BounceConfig(
            host="imap.example.com",
            port=993,
            user="bounces@example.com",
            password="secret",
        ))
        await proxy.start()  # Starts bounce_receiver automatically

Note:
    BounceReceiver correlates bounces with sent messages using the
    X-Genro-Mail-ID header and records events in message_events table.
"""

from .parser import BounceInfo, BounceParser
from .receiver import BounceConfig, BounceReceiver

__all__ = ["BounceConfig", "BounceInfo", "BounceParser", "BounceReceiver"]
