# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""SMTP account entity module.

This module provides the Account entity for managing SMTP server
configurations in a multi-tenant mail proxy environment.

Components:
    AccountsTable: Database table manager for account storage.
    AccountEndpoint: REST API endpoint for CRUD operations.

Example:
    Access via MailProxyBase::

        from core.mail_proxy.proxy_base import MailProxyBase

        proxy = MailProxyBase(db_path=":memory:")
        await proxy.init()

        # Table access
        accounts_table = proxy.db.table("accounts")
        await accounts_table.add({
            "id": "main",
            "tenant_id": "acme",
            "host": "smtp.gmail.com",
            "port": 587,
        })

        # Endpoint access (for API/CLI)
        from core.mail_proxy.entities.account import AccountEndpoint
        endpoint = AccountEndpoint(accounts_table)

Note:
    Enterprise Edition (EE) extends both classes with PEC support
    via AccountsTable_EE and AccountEndpoint_EE mixins.
"""

from .endpoint import AccountEndpoint
from .table import AccountsTable

__all__ = ["AccountEndpoint", "AccountsTable"]
