# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""HTTP client for connecting to mail-proxy servers.

Example:
    >>> from tools.http_client import MailProxyClient, connect
    >>> proxy = MailProxyClient("http://localhost:8000", token="secret")
    >>> proxy.status()
    {'ok': True, 'active': True}
"""

from .client import (
    Account,
    AccountsAPI,
    MailProxyClient,
    Message,
    MessagesAPI,
    Tenant,
    TenantsAPI,
    connect,
    register_connection,
)

__all__ = [
    "Account",
    "AccountsAPI",
    "MailProxyClient",
    "Message",
    "MessagesAPI",
    "Tenant",
    "TenantsAPI",
    "connect",
    "register_connection",
]
