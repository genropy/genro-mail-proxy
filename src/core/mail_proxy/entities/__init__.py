# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Domain entities for the mail proxy service.

Each subdirectory contains:
- table.py: SQL table manager
- schema.py: Pydantic schemas for validation
- README.md: Entity documentation
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
