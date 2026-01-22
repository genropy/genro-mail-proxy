# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Table managers for the mail service database.

DEPRECATED: This module is kept for backward compatibility.
New code should import from async_mail_service.entities instead.
"""

from ..entities import (
    AccountsTable,
    InstanceConfigTable,
    MessagesTable,
    SendLogTable,
    TenantsTable,
)

__all__ = [
    "AccountsTable",
    "InstanceConfigTable",
    "MessagesTable",
    "SendLogTable",
    "TenantsTable",
]
