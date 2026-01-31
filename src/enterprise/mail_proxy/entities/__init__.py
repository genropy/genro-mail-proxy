# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: BSL-1.1
"""Enterprise Edition composed table and endpoint classes.

This module composes CE (core) base classes with EE-specific mixins using
multiple inheritance, providing full functionality in Enterprise Edition.

Components:
    TenantsTable: CE + per-tenant API key management.
    AccountsTable: CE + PEC account configuration and IMAP polling.
    MessagesTable: CE + PEC receipt tracking.
    InstanceTable: CE + bounce detection configuration.
    AccountEndpoint: CE + PEC account management API.

Example:
    In EE mode, MailProxyDb uses composed tables automatically::

        from enterprise.mail_proxy.entities import AccountsTable

        # AccountsTable includes both CE and EE methods
        accounts = AccountsTable(db)
        await accounts.add_pec_account(
            tenant_id="acme",
            account_id="pec-1",
            smtp_host="smtps.pec.aruba.it",
            pec_imap_host="imaps.pec.aruba.it",
            ...
        )

Note:
    The composition pattern: `class X(X_EE, CoreX): pass` ensures EE methods
    take precedence (MRO) while CE provides the base implementation.
    Import from this module when running in EE mode.
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
