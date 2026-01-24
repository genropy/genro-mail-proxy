# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Core orchestration module for the mail proxy service.

This package provides the main MailProxy class and related components
for orchestrating email dispatch, delivery reporting, and lifecycle management.

The module is split into focused submodules:
- proxy: Main MailProxy class, lifecycle, and public API
- dispatcher: SMTP dispatch loop, email building, and sending
- reporting: Delivery report management and client synchronization
"""

from .proxy import (
    DEFAULT_PRIORITY,
    LABEL_TO_PRIORITY,
    PRIORITY_LABELS,
    AccountConfigurationError,
    AttachmentTooLargeError,
    MailProxy,
)

__all__ = [
    "MailProxy",
    "AccountConfigurationError",
    "AttachmentTooLargeError",
    "PRIORITY_LABELS",
    "LABEL_TO_PRIORITY",
    "DEFAULT_PRIORITY",
]
