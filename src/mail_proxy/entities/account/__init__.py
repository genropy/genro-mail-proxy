# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Account entity: SMTP account configurations."""

from .schema import Account, AccountCreate, AccountListItem, AccountUpdate
from .table import AccountsTable

__all__ = [
    "Account",
    "AccountCreate",
    "AccountListItem",
    "AccountsTable",
    "AccountUpdate",
]
