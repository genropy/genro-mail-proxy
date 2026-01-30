# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: BSL-1.1
"""Enterprise mail_proxy package (Enterprise Edition features).

This package contains all Enterprise Edition functionality:
- bounce/: Bounce detection via IMAP polling
- pec/: PEC (Posta Elettronica Certificata) receipt handling
- attachments/: Large file storage (S3/GCS/Azure)
- entities/: EE extensions for database tables
- proxy_ee: MailProxy_EE mixin with bounce detection
"""

from .proxy_ee import MailProxy_EE

__all__ = ["MailProxy_EE"]
