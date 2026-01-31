# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: BSL-1.1
"""PEC receipt parser for Italian certified email receipts.

PEC receipts are standardized messages sent by PEC providers:
- Ricevuta di accettazione: confirms the PEC system accepted the message
- Ricevuta di avvenuta consegna: confirms delivery to recipient's PEC mailbox
- Ricevuta di mancata consegna: delivery failed (recipient doesn't exist, etc.)
- Avviso di non accettazione: message was rejected by the PEC system

Receipts contain:
- X-Ricevuta header indicating receipt type
- Original message ID in X-Riferimento-Message-ID or embedded headers
- Timestamp of the event
- Error details for failure receipts
"""

from __future__ import annotations

import email
import re
from dataclasses import dataclass
from email.message import Message
from typing import Literal

PecReceiptType = Literal[
    "accettazione",  # Acceptance by sender's PEC provider
    "consegna",  # Delivery to recipient's mailbox
    "mancata_consegna",  # Delivery failure
    "non_accettazione",  # Rejected by PEC system
    "presa_in_carico",  # Taken in charge (intermediate step)
]


@dataclass
class PecReceiptInfo:
    """Parsed PEC receipt information."""

    original_message_id: str | None  # X-Genro-Mail-ID from original message
    receipt_type: PecReceiptType | None
    timestamp: str | None  # ISO timestamp from receipt
    error_reason: str | None  # For failure receipts
    recipient: str | None  # Recipient email address


class PecReceiptParser:
    """Parse PEC receipt messages."""

    # Map X-Ricevuta header values to receipt types
    RECEIPT_TYPE_MAP: dict[str, PecReceiptType] = {
        "accettazione": "accettazione",
        "avvenuta-consegna": "consegna",
        "consegna": "consegna",
        "mancata-consegna": "mancata_consegna",
        "errore-consegna": "mancata_consegna",
        "non-accettazione": "non_accettazione",
        "presa-in-carico": "presa_in_carico",
    }

    # Subject patterns for PEC receipts
    RECEIPT_SUBJECT_PATTERNS = [
        (re.compile(r"ACCETTAZIONE:", re.I), "accettazione"),
        (re.compile(r"AVVENUTA\s+CONSEGNA:", re.I), "consegna"),
        (re.compile(r"POSTA\s+CERTIFICATA:\s+AVVENUTA\s+CONSEGNA", re.I), "consegna"),
        (re.compile(r"MANCATA\s+CONSEGNA:", re.I), "mancata_consegna"),
        (re.compile(r"ERRORE\s+CONSEGNA:", re.I), "mancata_consegna"),
        (re.compile(r"NON\s+ACCETTAZIONE:", re.I), "non_accettazione"),
        (re.compile(r"PRESA\s+IN\s+CARICO:", re.I), "presa_in_carico"),
    ]

    def parse(self, raw_email: bytes) -> PecReceiptInfo:
        """Parse a PEC receipt message."""
        msg = email.message_from_bytes(raw_email)
        return self._parse_message(msg)

    def _parse_message(self, msg: Message) -> PecReceiptInfo:
        """Extract PEC receipt information from message."""
        receipt_type = self._detect_receipt_type(msg)

        if receipt_type is None:
            # Not a PEC receipt
            return PecReceiptInfo(
                original_message_id=None,
                receipt_type=None,
                timestamp=None,
                error_reason=None,
                recipient=None,
            )

        original_id = self._extract_original_id(msg)
        timestamp = self._extract_timestamp(msg)
        error_reason = (
            self._extract_error_reason(msg)
            if receipt_type in ("mancata_consegna", "non_accettazione")
            else None
        )
        recipient = self._extract_recipient(msg)

        return PecReceiptInfo(
            original_message_id=original_id,
            receipt_type=receipt_type,
            timestamp=timestamp,
            error_reason=error_reason,
            recipient=recipient,
        )

    def _detect_receipt_type(self, msg: Message) -> PecReceiptType | None:
        """Detect if message is a PEC receipt and its type."""
        # Check X-Ricevuta header (standard PEC header)
        x_ricevuta = msg.get("X-Ricevuta", "").lower().strip()
        if x_ricevuta and x_ricevuta in self.RECEIPT_TYPE_MAP:
            return self.RECEIPT_TYPE_MAP[x_ricevuta]

        # Check X-Trasporto header (another PEC indicator)
        x_trasporto = msg.get("X-Trasporto", "").lower()
        if "posta-certificata" in x_trasporto:
            # It's a PEC message, check subject for receipt type
            pass

        # Fallback: check subject patterns
        subject = msg.get("Subject", "")
        for pattern, rtype in self.RECEIPT_SUBJECT_PATTERNS:
            if pattern.search(subject):
                # rtype is already a PecReceiptType literal from the tuple
                return rtype  # type: ignore[return-value]

        return None

    def _extract_original_id(self, msg: Message) -> str | None:
        """Extract original message ID from PEC receipt."""
        # Check X-Riferimento-Message-ID header
        ref_id = msg.get("X-Riferimento-Message-ID")
        if ref_id:
            # Try to extract our X-Genro-Mail-ID from the referenced message
            # The ref_id is the Message-ID of the original, not our tracking ID
            pass

        # Search for X-Genro-Mail-ID in the receipt body or attached original
        for part in msg.walk():
            # Check if this part contains our tracking header
            genro_id = part.get("X-Genro-Mail-ID")
            if genro_id:
                return genro_id.strip()

            # Check body text for the header
            content_type = part.get_content_type()
            if content_type in ("text/plain", "text/html", "message/rfc822"):
                payload = part.get_payload(decode=True)
                if isinstance(payload, bytes):
                    text = payload.decode("utf-8", errors="replace")
                    match = re.search(r"X-Genro-Mail-ID:\s*(\S+)", text, re.I)
                    if match:
                        return match.group(1).strip()

        return None

    def _extract_timestamp(self, msg: Message) -> str | None:
        """Extract receipt timestamp."""
        # Check Date header
        date_str = msg.get("Date")
        if date_str:
            return date_str

        # Check X-Data-Ricevuta header (PEC-specific)
        data_ricevuta = msg.get("X-Data-Ricevuta")
        if data_ricevuta:
            return data_ricevuta

        return None

    def _extract_error_reason(self, msg: Message) -> str | None:
        """Extract error reason from failure receipts."""
        # Check X-Errore header
        errore = msg.get("X-Errore")
        if errore:
            return errore[:500]

        # Search body for error description
        body = self._get_text_body(msg)
        if body:
            # Common error patterns in PEC failure receipts
            patterns = [
                re.compile(r"Errore:\s*(.+?)(?:\n|$)", re.I),
                re.compile(r"Motivo:\s*(.+?)(?:\n|$)", re.I),
                re.compile(r"Causa:\s*(.+?)(?:\n|$)", re.I),
            ]
            for pattern in patterns:
                match = pattern.search(body)
                if match:
                    return match.group(1).strip()[:500]

        return None

    def _extract_recipient(self, msg: Message) -> str | None:
        """Extract recipient email from receipt."""
        # Check X-Destinatario header
        destinatario = msg.get("X-Destinatario")
        if destinatario:
            return destinatario.strip()

        # Check To header of the original (often in subject)
        subject = msg.get("Subject", "")
        email_pattern = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")
        match = email_pattern.search(subject)
        if match:
            return match.group(0)

        return None

    def _get_text_body(self, msg: Message) -> str:
        """Extract text body from message."""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    payload = part.get_payload(decode=True)
                    if isinstance(payload, bytes):
                        return payload.decode("utf-8", errors="replace")
            return ""
        else:
            payload = msg.get_payload(decode=True)
            if isinstance(payload, bytes):
                return payload.decode("utf-8", errors="replace")
            return str(payload) if payload else ""


__all__ = ["PecReceiptInfo", "PecReceiptParser", "PecReceiptType"]
