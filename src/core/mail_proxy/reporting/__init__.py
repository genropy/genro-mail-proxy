# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Client reporting subsystem.

This package provides the ClientReporter component for delivery report
synchronization with upstream clients.

Usage:
    from core.mail_proxy.reporting import ClientReporter

    # ClientReporter is instantiated by MailProxy
    proxy.client_reporter.start()
    proxy.client_reporter.stop()
"""

from .client_reporter import DEFAULT_SYNC_INTERVAL, ClientReporter

__all__ = ["ClientReporter", "DEFAULT_SYNC_INTERVAL"]
