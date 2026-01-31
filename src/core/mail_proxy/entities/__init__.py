# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Domain entities for the mail proxy service.

This package contains all database table managers for the mail proxy.
Each entity subdirectory provides a table.py (database operations) and
optionally an endpoint.py (REST API operations).

Tables:
    AccountsTable: SMTP account configurations with encrypted passwords.
    CommandLogTable: Audit log for API command tracking.
    InstanceTable: Singleton service configuration.
    MessageEventTable: Delivery event tracking (sent, error, deferred).
    MessagesTable: Email queue with priority and scheduling.
    TenantsTable: Multi-tenant configuration and batch suspension.

Example:
    Access tables via MailProxyDb::

        from core.mail_proxy.mailproxy_db import MailProxyDb

        db = MailProxyDb(db_path=":memory:")
        await db.init()

        # Access tables by name
        accounts = db.table("accounts")
        messages = db.table("messages")
        tenants = db.table("tenants")

        # Or directly by class
        from core.mail_proxy.entities import AccountsTable
        accounts_table = db.table("accounts")  # Same as above

Note:
    Enterprise Edition (EE) extends these tables with additional
    functionality via mixin classes (e.g., AccountsTable_EE for PEC).
"""

from .account.table import AccountsTable
from .command_log.table import CommandLogTable
from .instance.table import InstanceTable
from .message.table import MessagesTable
from .message_event.table import MessageEventTable
from .tenant.table import TenantsTable

__all__ = [
    "AccountsTable",
    "CommandLogTable",
    "InstanceTable",
    "MessageEventTable",
    "MessagesTable",
    "TenantsTable",
]
