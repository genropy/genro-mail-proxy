# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""SMTP dispatch subsystem for email delivery.

This package provides the complete email sending pipeline including
connection pooling, rate limiting, retry logic, and attachment handling.

Components:
    SmtpSender: Central coordinator for SMTP dispatch operations.
    SMTPPool: Connection pool with acquire/release semantics.
    RateLimiter: Per-account sliding-window rate limiting.
    RetryStrategy: Configurable retry with exponential backoff.
    AttachmentManager: Multi-backend attachment fetching.
    TieredCache: Memory + disk cache for attachment content.

Example:
    SmtpSender is instantiated and managed by MailProxy::

        from core.mail_proxy.proxy import MailProxy

        proxy = MailProxy(db_path="mail.db")
        await proxy.start()  # Starts smtp_sender automatically

        # Trigger immediate dispatch
        proxy.smtp_sender.wake()

        await proxy.stop()  # Stops smtp_sender gracefully

Note:
    All SMTP operations are asynchronous. The dispatch loop runs
    continuously, fetching ready messages and attempting delivery
    with automatic retry on temporary failures.
"""

from .attachments import AttachmentManager
from .cache import TieredCache
from .pool import SMTPPool
from .rate_limiter import RateLimiter
from .retry import DEFAULT_MAX_RETRIES, DEFAULT_RETRY_DELAYS, RetryStrategy
from .sender import AccountConfigurationError, AttachmentTooLargeError, SmtpSender

__all__ = [
    "SmtpSender",
    "SMTPPool",
    "RateLimiter",
    "RetryStrategy",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_RETRY_DELAYS",
    "AccountConfigurationError",
    "AttachmentTooLargeError",
    "AttachmentManager",
    "TieredCache",
]
