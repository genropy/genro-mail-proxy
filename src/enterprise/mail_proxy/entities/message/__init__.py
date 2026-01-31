# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: BSL-1.1
"""Enterprise message entity with PEC tracking.

This package extends the core messages table with PEC-specific
tracking for receipt lifecycle (acceptance, delivery, failure).

Components:
    MessagesTable_EE: Mixin adding PEC tracking methods.

Example:
    Find PEC messages missing acceptance receipts::

        cutoff = int(time.time()) - 1800  # 30 minutes ago
        messages = await db.table("messages").get_pec_without_acceptance(cutoff)
        for msg in messages:
            await report_pec_timeout(msg["tenant_id"], msg["id"])

Note:
    PEC messages without acceptance within 30 minutes are flagged
    for timeout alert. Delivery receipts complete the PEC lifecycle.
"""

from .table_ee import MessagesTable_EE

__all__ = ["MessagesTable_EE"]
