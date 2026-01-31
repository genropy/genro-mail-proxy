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


class TestPecReceiptParserXTrasporto:
    """Tests for X-Trasporto header handling (line 117)."""

    @pytest.fixture
    def parser(self):
        return PecReceiptParser()

    def test_x_trasporto_with_subject_fallback(self, parser):
        """X-Trasporto posta-certificata triggers subject check when no X-Ricevuta."""
        msg = MIMEText("Ricevuta PEC.\n\nX-Genro-Mail-ID: msg-trasporto-001", "plain")
        msg["From"] = "pec@pec.test.it"
        msg["To"] = "sender@pec.test.it"
        msg["Subject"] = "ACCETTAZIONE: Test via X-Trasporto"
        msg["X-Trasporto"] = "posta-certificata"
        # No X-Ricevuta header

        info = parser.parse(msg.as_bytes())

        assert info.receipt_type == "accettazione"
        assert info.original_message_id == "msg-trasporto-001"


class TestPecReceiptParserXGenroMailIdHeader:
    """Tests for X-Genro-Mail-ID as direct header (line 142)."""

    @pytest.fixture
    def parser(self):
        return PecReceiptParser()

    def test_extract_genro_id_from_header(self, parser):
        """Extract X-Genro-Mail-ID from message part header."""
        # Create multipart message with X-Genro-Mail-ID as header on a part
        msg = MIMEMultipart()
        msg["From"] = "pec@pec.test.it"
        msg["Subject"] = "ACCETTAZIONE: Test"
        msg["X-Ricevuta"] = "accettazione"

        # Add a text part with X-Genro-Mail-ID as header
        text_part = MIMEText("Body content without ID in text", "plain")
        text_part["X-Genro-Mail-ID"] = "msg-header-direct-001"
        msg.attach(text_part)

        info = parser.parse(msg.as_bytes())

        assert info.original_message_id == "msg-header-direct-001"


class TestPecReceiptParserXDataRicevuta:
    """Tests for X-Data-Ricevuta timestamp (line 166)."""

    @pytest.fixture
    def parser(self):
        return PecReceiptParser()

    def test_extract_timestamp_from_x_data_ricevuta(self, parser):
        """Extract timestamp from X-Data-Ricevuta when no Date header."""
        msg = MIMEText("Body", "plain")
        msg["From"] = "pec@pec.test.it"
        msg["Subject"] = "ACCETTAZIONE: Test"
        msg["X-Ricevuta"] = "accettazione"
        # No Date header, but X-Data-Ricevuta present
        msg["X-Data-Ricevuta"] = "15/01/2025 14:30:00"

        info = parser.parse(msg.as_bytes())

        assert info.timestamp == "15/01/2025 14:30:00"


class TestPecReceiptParserErrorPatterns:
    """Tests for error extraction patterns (lines 179-191)."""

    @pytest.fixture
    def parser(self):
        return PecReceiptParser()

    def test_extract_error_with_motivo_pattern(self, parser):
        """Extract error using 'Motivo:' pattern."""
        msg = MIMEText(
            "La consegna non è avvenuta.\n\nMotivo: Casella PEC piena\n\nContattare il destinatario.",
            "plain",
        )
        msg["From"] = "pec@pec.test.it"
        msg["Subject"] = "MANCATA CONSEGNA: Test"
        msg["X-Ricevuta"] = "mancata-consegna"

        info = parser.parse(msg.as_bytes())

        assert info.error_reason is not None
        assert "Casella PEC piena" in info.error_reason

    def test_extract_error_with_causa_pattern(self, parser):
        """Extract error using 'Causa:' pattern."""
        msg = MIMEText(
            "Impossibile consegnare il messaggio.\n\nCausa: Indirizzo non esistente\n\nFine.",
            "plain",
        )
        msg["From"] = "pec@pec.test.it"
        msg["Subject"] = "MANCATA CONSEGNA: Test"
        msg["X-Ricevuta"] = "mancata-consegna"

        info = parser.parse(msg.as_bytes())

        assert info.error_reason is not None
        assert "Indirizzo non esistente" in info.error_reason


class TestPecReceiptParserGetTextBody:
    """Tests for _get_text_body edge cases (lines 212-217, 222)."""

    @pytest.fixture
    def parser(self):
        return PecReceiptParser()

    def test_get_text_body_multipart_without_text_plain(self, parser):
        """_get_text_body returns empty string when multipart has no text/plain."""
        msg = MIMEMultipart()
        msg["From"] = "pec@pec.test.it"
        msg["Subject"] = "MANCATA CONSEGNA: Test"
        msg["X-Ricevuta"] = "mancata-consegna"
        # Add only HTML part, no text/plain
        html_part = MIMEText("<html><body>Error</body></html>", "html")
        msg.attach(html_part)

        info = parser.parse(msg.as_bytes())

        # Should not crash, error_reason will be None since no text body found
        assert info.receipt_type == "mancata_consegna"

    def test_get_text_body_non_multipart_string_payload(self, parser):
        """_get_text_body handles non-bytes payload (str) on non-multipart."""
        # Create a message where payload is a string not bytes
        import email

        raw = b"From: pec@pec.test.it\r\nSubject: MANCATA CONSEGNA: Test\r\nX-Ricevuta: mancata-consegna\r\n\r\nErrore: Test error string"
        msg = email.message_from_bytes(raw)

        info = parser.parse(raw)

        assert info.receipt_type == "mancata_consegna"
        # The error should be extracted from the string body
        assert info.error_reason is not None
        assert "Test error string" in info.error_reason


class TestPecReceiptParserXRiferimentoMessageId:
    """Tests for X-Riferimento-Message-ID header handling (line 135)."""

    @pytest.fixture
    def parser(self):
        return PecReceiptParser()

    def test_x_riferimento_message_id_without_genro_id(self, parser):
        """X-Riferimento-Message-ID present but no X-Genro-Mail-ID found."""
        msg = MIMEText("Ricevuta PEC senza X-Genro-Mail-ID nel body.", "plain")
        msg["From"] = "pec@pec.test.it"
        msg["Subject"] = "ACCETTAZIONE: Test"
        msg["X-Ricevuta"] = "accettazione"
        msg["X-Riferimento-Message-ID"] = "<original@message.id>"

        info = parser.parse(msg.as_bytes())

        # Should parse without error, original_message_id will be None
        assert info.receipt_type == "accettazione"
        assert info.original_message_id is None


class TestPecReceiptParserPayloadEdgeCases:
    """Tests for payload edge cases (lines 148, 214-216, 222)."""

    @pytest.fixture
    def parser(self):
        return PecReceiptParser()

    def test_extract_original_id_from_rfc822_part(self, parser):
        """Extract X-Genro-Mail-ID from embedded message/rfc822 part."""
        from email.mime.message import MIMEMessage

        # Create the main message
        msg = MIMEMultipart()
        msg["From"] = "pec@pec.test.it"
        msg["Subject"] = "ACCETTAZIONE: Test"
        msg["X-Ricevuta"] = "accettazione"

        # Create embedded original message with X-Genro-Mail-ID in body
        original = MIMEText("Original message content\nX-Genro-Mail-ID: msg-embedded-rfc822", "plain")
        original["From"] = "sender@test.com"
        original["Subject"] = "Original"

        # Attach as message/rfc822
        rfc822_part = MIMEMessage(original)
        msg.attach(rfc822_part)

        info = parser.parse(msg.as_bytes())

        assert info.original_message_id == "msg-embedded-rfc822"

    def test_multipart_text_plain_with_none_payload(self, parser):
        """Multipart message where text/plain part has None payload."""
        # Manually construct a message where get_payload(decode=True) might return None
        # This happens when the part has encoding issues
        raw = b"""MIME-Version: 1.0
From: pec@pec.test.it
Subject: MANCATA CONSEGNA: Test
X-Ricevuta: mancata-consegna
Content-Type: multipart/mixed; boundary="boundary123"

--boundary123
Content-Type: text/plain; charset=utf-8
Content-Transfer-Encoding: base64

SW52YWxpZCBiYXNlNjQ=
--boundary123--
"""
        info = parser.parse(raw)

        # Should parse without error
        assert info.receipt_type == "mancata_consegna"
