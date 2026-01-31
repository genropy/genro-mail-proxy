# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Unit tests for PecReceiptParser."""

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate

import pytest

from enterprise.mail_proxy.pec.parser import PecReceiptInfo, PecReceiptParser


class TestPecReceiptParserAccettazione:
    """Tests for accettazione (acceptance) receipts."""

    @pytest.fixture
    def parser(self):
        return PecReceiptParser()

    def _create_pec_receipt(
        self,
        receipt_type: str = "accettazione",
        subject: str = "ACCETTAZIONE: Test message",
        x_genro_id: str | None = "msg-test-001",
        recipient: str | None = None,
        error: str | None = None,
    ) -> bytes:
        """Create a PEC receipt message."""
        msg = MIMEMultipart()
        msg["From"] = "pec-provider@pec.test.it"
        msg["To"] = "sender@pec.test.it"
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=True)
        msg["X-Ricevuta"] = receipt_type
        msg["X-Trasporto"] = "posta-certificata"

        if recipient:
            msg["X-Destinatario"] = recipient

        if error:
            msg["X-Errore"] = error

        # Body with original message reference
        body_text = "Ricevuta di posta certificata.\n"
        if x_genro_id:
            body_text += f"\nX-Genro-Mail-ID: {x_genro_id}\n"
        body = MIMEText(body_text, "plain")
        msg.attach(body)

        return msg.as_bytes()

    def test_parse_accettazione(self, parser):
        """Parse accettazione receipt."""
        raw = self._create_pec_receipt(
            receipt_type="accettazione",
            subject="ACCETTAZIONE: Test message",
        )
        info = parser.parse(raw)

        assert info.receipt_type == "accettazione"
        assert info.original_message_id == "msg-test-001"
        assert info.timestamp is not None

    def test_parse_consegna(self, parser):
        """Parse avvenuta consegna receipt."""
        raw = self._create_pec_receipt(
            receipt_type="avvenuta-consegna",
            subject="POSTA CERTIFICATA: AVVENUTA CONSEGNA",
            recipient="dest@pec.test.it",
        )
        info = parser.parse(raw)

        assert info.receipt_type == "consegna"
        assert info.recipient == "dest@pec.test.it"

    def test_parse_mancata_consegna(self, parser):
        """Parse mancata consegna (delivery failure) receipt."""
        raw = self._create_pec_receipt(
            receipt_type="mancata-consegna",
            subject="MANCATA CONSEGNA: Test message",
            recipient="unknown@pec.test.it",
            error="Destinatario sconosciuto",
        )
        info = parser.parse(raw)

        assert info.receipt_type == "mancata_consegna"
        assert info.error_reason == "Destinatario sconosciuto"

    def test_parse_non_accettazione(self, parser):
        """Parse non accettazione (rejection) receipt."""
        raw = self._create_pec_receipt(
            receipt_type="non-accettazione",
            subject="NON ACCETTAZIONE: Test message",
            error="Messaggio rifiutato",
        )
        info = parser.parse(raw)

        assert info.receipt_type == "non_accettazione"
        assert info.error_reason == "Messaggio rifiutato"

    def test_parse_presa_in_carico(self, parser):
        """Parse presa in carico receipt."""
        raw = self._create_pec_receipt(
            receipt_type="presa-in-carico",
            subject="PRESA IN CARICO: Test message",
        )
        info = parser.parse(raw)

        assert info.receipt_type == "presa_in_carico"


class TestPecReceiptParserSubjectFallback:
    """Tests for subject-based receipt detection."""

    @pytest.fixture
    def parser(self):
        return PecReceiptParser()

    def _create_pec_without_header(
        self,
        subject: str,
        x_genro_id: str | None = "msg-test-001",
    ) -> bytes:
        """Create PEC receipt without X-Ricevuta header."""
        msg = MIMEText(f"Ricevuta PEC.\n\nX-Genro-Mail-ID: {x_genro_id}", "plain")
        msg["From"] = "pec@pec.test.it"
        msg["To"] = "sender@pec.test.it"
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=True)
        return msg.as_bytes()

    def test_detect_accettazione_by_subject(self, parser):
        """Detect accettazione from subject."""
        raw = self._create_pec_without_header("ACCETTAZIONE: Oggetto messaggio")
        info = parser.parse(raw)

        assert info.receipt_type == "accettazione"

    def test_detect_consegna_by_subject(self, parser):
        """Detect consegna from subject."""
        subjects = [
            "AVVENUTA CONSEGNA: Test",
            "POSTA CERTIFICATA: AVVENUTA CONSEGNA - Test",
        ]
        for subject in subjects:
            raw = self._create_pec_without_header(subject)
            info = parser.parse(raw)
            assert info.receipt_type == "consegna", f"Failed for: {subject}"

    def test_detect_mancata_consegna_by_subject(self, parser):
        """Detect mancata consegna from subject."""
        subjects = [
            "MANCATA CONSEGNA: Test",
            "ERRORE CONSEGNA: Test",
        ]
        for subject in subjects:
            raw = self._create_pec_without_header(subject)
            info = parser.parse(raw)
            assert info.receipt_type == "mancata_consegna", f"Failed for: {subject}"

    def test_non_pec_message(self, parser):
        """Regular message is not detected as PEC receipt."""
        msg = MIMEText("Normal email content", "plain")
        msg["From"] = "friend@test.com"
        msg["To"] = "me@test.com"
        msg["Subject"] = "Hello there"

        info = parser.parse(msg.as_bytes())

        assert info.receipt_type is None
        assert info.original_message_id is None


class TestPecReceiptParserExtraction:
    """Tests for field extraction."""

    @pytest.fixture
    def parser(self):
        return PecReceiptParser()

    def test_extract_x_genro_mail_id_from_body(self, parser):
        """Extract X-Genro-Mail-ID from body text."""
        msg = MIMEText(
            "Ricevuta PEC.\n\nHeaders originali:\nX-Genro-Mail-ID: msg-12345\nFrom: sender@test.com",
            "plain",
        )
        msg["From"] = "pec@pec.test.it"
        msg["Subject"] = "ACCETTAZIONE: Test"
        msg["X-Ricevuta"] = "accettazione"

        info = parser.parse(msg.as_bytes())

        assert info.original_message_id == "msg-12345"

    def test_extract_recipient_from_header(self, parser):
        """Extract recipient from X-Destinatario header."""
        msg = MIMEText("Body", "plain")
        msg["From"] = "pec@pec.test.it"
        msg["Subject"] = "AVVENUTA CONSEGNA: Test"
        msg["X-Ricevuta"] = "consegna"
        msg["X-Destinatario"] = "recipient@pec.test.it"

        info = parser.parse(msg.as_bytes())

        assert info.recipient == "recipient@pec.test.it"

    def test_extract_recipient_from_subject(self, parser):
        """Extract recipient email from subject when no header."""
        msg = MIMEText("Body", "plain")
        msg["From"] = "pec@pec.test.it"
        msg["Subject"] = "MANCATA CONSEGNA a dest@pec.example.it"
        msg["X-Ricevuta"] = "mancata-consegna"

        info = parser.parse(msg.as_bytes())

        assert info.recipient == "dest@pec.example.it"

    def test_extract_error_from_body(self, parser):
        """Extract error reason from body."""
        msg = MIMEText(
            "La consegna del messaggio non è avvenuta.\n\nErrore: Casella inesistente\n\nDettagli...",
            "plain",
        )
        msg["From"] = "pec@pec.test.it"
        msg["Subject"] = "MANCATA CONSEGNA: Test"
        msg["X-Ricevuta"] = "mancata-consegna"

        info = parser.parse(msg.as_bytes())

        assert info.error_reason is not None
        assert "Casella inesistente" in info.error_reason

    def test_extract_timestamp_from_date(self, parser):
        """Extract timestamp from Date header."""
        msg = MIMEText("Body", "plain")
        msg["From"] = "pec@pec.test.it"
        msg["Subject"] = "ACCETTAZIONE: Test"
        msg["X-Ricevuta"] = "accettazione"
        msg["Date"] = "Mon, 20 Jan 2025 10:30:00 +0100"

        info = parser.parse(msg.as_bytes())

        assert info.timestamp is not None
        assert "20 Jan 2025" in info.timestamp


class TestPecReceiptInfo:
    """Tests for PecReceiptInfo dataclass."""

    def test_receipt_info_fields(self):
        """PecReceiptInfo has expected fields."""
        info = PecReceiptInfo(
            original_message_id="msg-123",
            receipt_type="consegna",
            timestamp="2025-01-20T10:30:00",
            error_reason=None,
            recipient="dest@pec.test.it",
        )

        assert info.original_message_id == "msg-123"
        assert info.receipt_type == "consegna"
        assert info.timestamp == "2025-01-20T10:30:00"
        assert info.error_reason is None
        assert info.recipient == "dest@pec.test.it"

    def test_receipt_info_with_error(self):
        """PecReceiptInfo with error reason."""
        info = PecReceiptInfo(
            original_message_id="msg-456",
            receipt_type="mancata_consegna",
            timestamp="2025-01-20T10:30:00",
            error_reason="Casella piena",
            recipient="full@pec.test.it",
        )

        assert info.receipt_type == "mancata_consegna"
        assert info.error_reason == "Casella piena"


class TestPecReceiptTypeMap:
    """Tests for receipt type mapping."""

    @pytest.fixture
    def parser(self):
        return PecReceiptParser()

    def test_type_map_variants(self, parser):
        """All X-Ricevuta variants are mapped."""
        expected_mappings = {
            "accettazione": "accettazione",
            "avvenuta-consegna": "consegna",
            "consegna": "consegna",
            "mancata-consegna": "mancata_consegna",
            "errore-consegna": "mancata_consegna",
            "non-accettazione": "non_accettazione",
            "presa-in-carico": "presa_in_carico",
        }

        for header_value, expected_type in expected_mappings.items():
            assert parser.RECEIPT_TYPE_MAP.get(header_value) == expected_type
