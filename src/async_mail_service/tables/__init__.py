# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Table managers for the mail service database."""

from .accounts import AccountsTable
from .instance_config import InstanceConfigTable
from .messages import MessagesTable
from .send_log import SendLogTable
from .tenants import TenantsTable

__all__ = [
    "AccountsTable",
    "InstanceConfigTable",
    "MessagesTable",
    "SendLogTable",
    "TenantsTable",
]
