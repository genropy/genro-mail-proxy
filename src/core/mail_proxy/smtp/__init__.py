# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""SMTP dispatch subsystem.

This package provides the SmtpSender component for email delivery:

- SmtpSender: Main coordinator for SMTP dispatch
- SMTPPool: Connection pool for SMTP clients
- RateLimiter: In-memory rate limiting
- RetryStrategy: Retry logic for failed deliveries
- AttachmentManager: Fetch attachments from multiple backends
- TieredCache: Two-level cache for attachment content

Usage:
    from core.mail_proxy.smtp import SmtpSender, AttachmentManager

    # SmtpSender is instantiated by MailProxy
    proxy.smtp_sender.start()
    proxy.smtp_sender.stop()
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
