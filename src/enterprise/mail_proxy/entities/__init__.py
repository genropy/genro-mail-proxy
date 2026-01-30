# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: BSL-1.1
"""Enterprise Edition composed table and endpoint classes.

Combines CE base classes with EE-specific methods via multiple inheritance.
Usage: Import these classes instead of core classes when running in EE mode.

The composition follows this pattern:
    class AccountsTable(AccountsTable_EE, CoreAccountsTable):
        pass

This gives AccountsTable all methods from both CE (core) and EE (mixin).
"""

from core.mail_proxy.entities.account.endpoint import (
    AccountEndpoint as CoreAccountEndpoint,
)
from core.mail_proxy.entities.account.table import AccountsTable as CoreAccountsTable
from core.mail_proxy.entities.instance.table import InstanceTable as CoreInstanceTable
from core.mail_proxy.entities.message.table import MessagesTable as CoreMessagesTable
from core.mail_proxy.entities.tenant.table import TenantsTable as CoreTenantsTable

from .account.endpoint_ee import AccountEndpoint_EE
from .account.table_ee import AccountsTable_EE
from .instance.table_ee import InstanceTable_EE
from .message.table_ee import MessagesTable_EE
from .tenant.table_ee import TenantsTable_EE


# --- Composed Tables ---

class TenantsTable(TenantsTable_EE, CoreTenantsTable):
    """Enterprise Edition TenantsTable with multi-tenant management."""
    pass


class AccountsTable(AccountsTable_EE, CoreAccountsTable):
    """Enterprise Edition AccountsTable with PEC/IMAP support."""
    pass


class MessagesTable(MessagesTable_EE, CoreMessagesTable):
    """Enterprise Edition MessagesTable with PEC tracking."""
    pass


class InstanceTable(InstanceTable_EE, CoreInstanceTable):
    """Enterprise Edition InstanceTable with bounce detection config."""
    pass


# --- Composed Endpoints ---

class AccountEndpoint(AccountEndpoint_EE, CoreAccountEndpoint):
    """Enterprise Edition AccountEndpoint with PEC account management."""
    pass


__all__ = [
    # Composed EE tables (use these in EE mode)
    "TenantsTable",
    "AccountsTable",
    "MessagesTable",
    "InstanceTable",
    # Composed EE endpoints (use these in EE mode)
    "AccountEndpoint",
    # EE mixins (for type hints and testing)
    "AccountEndpoint_EE",
    "AccountsTable_EE",
    "InstanceTable_EE",
    "MessagesTable_EE",
    "TenantsTable_EE",
]
