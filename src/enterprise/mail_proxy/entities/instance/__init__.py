# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: BSL-1.1
"""Enterprise instance entity with bounce detection.

This package extends the core instance table with bounce detection
configuration for monitoring delivery failures via IMAP.

Components:
    InstanceTable_EE: Mixin adding bounce detection columns and methods.
    InstanceEndpoint_EE: Mixin adding bounce configuration API.

Example:
    Configure bounce detection::

        await db.table("instance").set_bounce_config(
            imap_host="imap.example.com",
            imap_port=993,
            imap_user="bounces@example.com",
            imap_password="secret",
            return_path="bounces@example.com",
            enabled=True,
        )

Note:
    Bounce detection monitors a dedicated IMAP mailbox for DSN messages.
    Bounces are correlated with sent messages via X-Genro-Mail-ID header.
"""

from .endpoint_ee import InstanceEndpoint_EE
from .table_ee import InstanceTable_EE

__all__ = ["InstanceEndpoint_EE", "InstanceTable_EE"]
