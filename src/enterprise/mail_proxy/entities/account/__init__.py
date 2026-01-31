# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: BSL-1.1
"""Enterprise account entity with PEC and IMAP support.

This package extends the core accounts table with Italian certified
email (PEC) functionality and IMAP polling for receipt tracking.

Components:
    AccountsTable_EE: Mixin adding PEC/IMAP columns and methods.
    AccountEndpoint_EE: Mixin adding PEC account management API.

Example:
    Configure a PEC account::

        await db.table("accounts").add_pec_account({
            "id": "pec-1",
            "tenant_id": "acme",
            "smtp_host": "smtps.pec.aruba.it",
            "smtp_port": 465,
            "imap_host": "imaps.pec.aruba.it",
            "imap_port": 993,
            "imap_user": "info@pec.acme.it",
            "imap_password": "secret",
        })

Note:
    PEC accounts have is_pec_account=1 and require IMAP configuration
    for receipt polling (accettazione, consegna, mancata_consegna).
"""

from .endpoint_ee import AccountEndpoint_EE
from .table_ee import AccountsTable_EE

__all__ = ["AccountEndpoint_EE", "AccountsTable_EE"]
