# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Domain entities for the mail proxy service.

Each subdirectory contains:
- table.py: SQL table manager
- schema.py: Pydantic schemas for validation
- README.md: Entity documentation
"""

from .account.table import AccountsTable
from .instance.table import InstanceTable
from .instance_config.table import InstanceConfigTable
from .message.table import MessagesTable
from .send_log.table import SendLogTable
from .tenant.table import TenantsTable

__all__ = [
    "AccountsTable",
    "InstanceConfigTable",
    "InstanceTable",
    "MessagesTable",
    "SendLogTable",
    "TenantsTable",
]
