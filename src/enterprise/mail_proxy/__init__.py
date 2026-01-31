# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: BSL-1.1
"""Enterprise mail_proxy package (Enterprise Edition features).

This package contains all Enterprise Edition functionality:
- bounce/: Bounce detection via IMAP polling
- pec/: PEC (Posta Elettronica Certificata) receipt handling
- attachments/: Large file storage (S3/GCS/Azure)
- entities/: EE extensions for database tables
- storage/: Cloud storage backends (S3/GCS/Azure) via fsspec
- proxy_ee: MailProxy_EE mixin with bounce detection
"""

from .proxy_ee import MailProxy_EE


def is_ee_enabled() -> bool:
    """Check if Enterprise Edition is available and enabled.

    Returns True if the EE package is properly installed.
    """
    return True  # If this module is importable, EE is enabled


__all__ = ["MailProxy_EE", "is_ee_enabled"]
