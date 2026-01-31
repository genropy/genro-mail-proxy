# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: BSL-1.1
"""Enterprise Edition features for genro-mail-proxy.

This package extends the core mail-proxy with enterprise capabilities:
bounce detection, PEC receipt handling, and large file storage.

Components:
    MailProxy_EE: Mixin adding bounce detection to MailProxy.
    bounce/: IMAP polling for bounce notification detection.
    pec/: PEC (Italian certified email) receipt handling.
    attachments/: Large file storage via fsspec (S3/GCS/Azure).
    entities/: EE table mixins (AccountsTable_EE, TenantsTable_EE).
    imap/: Async IMAP client wrapper (aioimaplib).

Example:
    EE features are auto-enabled when this package is installed::

        from core.mail_proxy import MailProxy

        proxy = MailProxy(db_path="mail.db")
        # MailProxy automatically includes MailProxy_EE mixin

        # Configure bounce detection
        from enterprise.mail_proxy.bounce import BounceConfig
        proxy.configure_bounce_receiver(BounceConfig(
            host="imap.example.com",
            port=993,
            user="bounces@example.com",
            password="secret",
        ))
        await proxy.start()

Note:
    EE detection happens at import time in core.mail_proxy.__init__.
    The HAS_ENTERPRISE flag controls mixin composition.
"""

from .proxy_ee import MailProxy_EE


def is_ee_enabled() -> bool:
    """Check if Enterprise Edition is available and enabled.

    Returns:
        True if EE package is properly installed and importable.
    """
    return True  # If this module is importable, EE is enabled


__all__ = ["MailProxy_EE", "is_ee_enabled"]
