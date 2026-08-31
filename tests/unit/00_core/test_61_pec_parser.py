# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""Unit tests for PEC receipt parser."""

from __future__ import annotations

import pytest

from src.mail_proxy.pec.parser import PecReceiptParser


class TestPecReceiptParser:
    """Tests for PEC receipt parsing."""

    def test_parse_acceptance_receipt_by_header(self):
        """Parse acceptance receipt with X-Ricevuta header."""
        raw = b"""From: posta-certificata@provider.it
To: user@pec.it
Subject: ACCETTAZIONE: Test message
X-Ricevuta: accettazione
X-Genro-Mail-ID: msg-001

La ricevuta di accettazione..."""

        parser = PecReceiptParser()
        info = parser.parse(raw)

        assert info.receipt_type == "accettazione"
        assert info.original_message_id == "msg-001"

    def test_parse_delivery_receipt_by_header(self):
        """Parse delivery receipt with X-Ricevuta header."""
        raw = b"""From: posta-certificata@provider.it
To: user@pec.it
Subject: AVVENUTA CONSEGNA: Test message
X-Ricevuta: avvenuta-consegna
X-Genro-Mail-ID: msg-002

Il messaggio e stato consegnato..."""

        parser = PecReceiptParser()
        info = parser.parse(raw)

        assert info.receipt_type == "consegna"
        assert info.original_message_id == "msg-002"

    def test_parse_failure_receipt(self):
        """Parse delivery failure receipt."""
        raw = b"""From: posta-certificata@provider.it
To: user@pec.it
Subject: MANCATA CONSEGNA: Test message
X-Ricevuta: mancata-consegna
X-Genro-Mail-ID: msg-003
X-Errore: Destinatario sconosciuto

Errore: impossibile consegnare il messaggio."""

        parser = PecReceiptParser()
        info = parser.parse(raw)

        assert info.receipt_type == "mancata_consegna"
        assert info.original_message_id == "msg-003"
        assert info.error_reason == "Destinatario sconosciuto"

    def test_parse_receipt_by_subject_pattern(self):
        """Parse receipt using subject pattern when header missing."""
        raw = b"""From: posta-certificata@provider.it
To: user@pec.it
Subject: POSTA CERTIFICATA: AVVENUTA CONSEGNA per dest@pec.it
Date: Mon, 20 Jan 2025 10:30:00 +0100
X-Genro-Mail-ID: msg-004

Il messaggio e stato consegnato."""

        parser = PecReceiptParser()
        info = parser.parse(raw)

        assert info.receipt_type == "consegna"
        assert info.original_message_id == "msg-004"
        assert info.recipient == "dest@pec.it"

    def test_parse_non_pec_message(self):
        """Non-PEC messages should return None receipt type."""
        raw = b"""From: sender@example.com
To: user@example.com
Subject: Hello World

This is a regular email."""

        parser = PecReceiptParser()
        info = parser.parse(raw)

        assert info.receipt_type is None
        assert info.original_message_id is None

    def test_extract_genro_id_from_body(self):
        """Extract X-Genro-Mail-ID from message body."""
        raw = b"""From: posta-certificata@provider.it
To: user@pec.it
Subject: ACCETTAZIONE: Test
X-Ricevuta: accettazione

Messaggio originale:
X-Genro-Mail-ID: msg-in-body-005

Fine del messaggio."""

        parser = PecReceiptParser()
        info = parser.parse(raw)

        assert info.receipt_type == "accettazione"
        assert info.original_message_id == "msg-in-body-005"

    def test_parse_non_acceptance_receipt(self):
        """Parse non-acceptance (rejection) receipt."""
        raw = b"""From: posta-certificata@provider.it
To: user@pec.it
Subject: NON ACCETTAZIONE: Test message
X-Ricevuta: non-accettazione
X-Genro-Mail-ID: msg-006

Il messaggio non e stato accettato."""

        parser = PecReceiptParser()
        info = parser.parse(raw)

        assert info.receipt_type == "non_accettazione"
        assert info.original_message_id == "msg-006"

    def test_extract_timestamp(self):
        """Extract timestamp from Date header."""
        raw = b"""From: posta-certificata@provider.it
To: user@pec.it
Subject: ACCETTAZIONE: Test
X-Ricevuta: accettazione
Date: Mon, 20 Jan 2025 10:30:00 +0100
X-Genro-Mail-ID: msg-007

Ricevuta."""

        parser = PecReceiptParser()
        info = parser.parse(raw)

        assert info.timestamp == "Mon, 20 Jan 2025 10:30:00 +0100"
