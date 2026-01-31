# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: BSL-1.1
"""PEC (Posta Elettronica Certificata) receipt handling.

This package processes Italian certified email (PEC) receipts by polling
PEC accounts for incoming ricevute and tracking delivery status.

Components:
    PecReceiver: Background task polling PEC accounts for receipts.
    PecReceiptParser: Extracts receipt type and metadata from PEC messages.
    PecReceiptInfo: Parsed receipt data (type, timestamp, recipient).

Example:
    PEC accounts are configured per-tenant::

        await db.table("accounts").add(
            tenant_id="acme",
            account_id="pec-1",
            smtp_host="smtps.pec.aruba.it",
            smtp_port=465,
            is_pec=True,
            pec_imap_host="imaps.pec.aruba.it",
            pec_imap_port=993,
            pec_imap_user="info@pec.acme.it",
            pec_imap_password="secret",
        )

Note:
    PEC receipts include: accettazione, consegna, mancata_consegna,
    avviso_mancata_consegna, presa_in_carico, errore. Messages without
    acceptance within 30 minutes are marked as pec_timeout.
"""

from .parser import PecReceiptInfo, PecReceiptParser
from .receiver import PecReceiver

__all__ = ["PecReceiptInfo", "PecReceiptParser", "PecReceiver"]
