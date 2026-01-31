# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Unit tests for BounceParser."""

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate

import pytest

from enterprise.mail_proxy.bounce.parser import BounceInfo, BounceParser


class TestBounceParserDSN:
    """Tests for RFC 3464 DSN parsing."""

    @pytest.fixture
    def parser(self):
        return BounceParser()

    def _create_dsn_bounce(
        self,
        original_message_id: str = "<test-123@example.com>",
        recipient: str = "user@example.com",
        status_code: str = "5.1.1",
        diagnostic: str = "550 User unknown",
        action: str = "failed",
    ) -> bytes:
        """Create a standard DSN bounce message."""
        msg = MIMEMultipart("report", report_type="delivery-status")
        msg["From"] = "mailer-daemon@test.local"
        msg["To"] = "sender@test.local"
        msg["Subject"] = "Undelivered Mail Returned to Sender"
        msg["Date"] = formatdate(localtime=True)

        # Part 1: Human-readable
        human = MIMEText("Your message could not be delivered.", "plain")
        msg.attach(human)

        # Part 2: Delivery status
        dsn_text = f"""Reporting-MTA: dns; test.local
Arrival-Date: {formatdate(localtime=True)}

Final-Recipient: rfc822; {recipient}
Action: {action}
Status: {status_code}
Diagnostic-Code: smtp; {diagnostic}
"""
        dsn = MIMEText(dsn_text, "delivery-status")
        msg.attach(dsn)

        # Part 3: Original headers
        original = f"""Message-ID: {original_message_id}
X-Genro-Mail-ID: msg-test-001
From: sender@test.local
To: {recipient}
Subject: Test
"""
        headers = MIMEText(original, "rfc822-headers")
        msg.attach(headers)

        return msg.as_bytes()

    def test_parse_hard_bounce_dsn(self, parser):
        """Parse DSN with 5xx status code as hard bounce."""
        raw = self._create_dsn_bounce(
            recipient="unknown@test.com",
            status_code="5.1.1",
            diagnostic="550 User unknown",
        )
        info = parser.parse(raw)

        assert info.bounce_type == "hard"
        assert info.recipient == "unknown@test.com"
        assert "550" in (info.bounce_code or "") or "511" in (info.bounce_code or "")
        assert info.bounce_reason is not None
        assert "User unknown" in info.bounce_reason

    def test_parse_soft_bounce_dsn(self, parser):
        """Parse DSN with 4xx status code as soft bounce."""
        raw = self._create_dsn_bounce(
            recipient="full@test.com",
            status_code="4.5.2",
            diagnostic="452 Mailbox full",
            action="delayed",
        )
        info = parser.parse(raw)

        assert info.bounce_type == "soft"
        assert info.recipient == "full@test.com"

    def test_extract_original_message_id(self, parser):
        """Extract X-Genro-Mail-ID from DSN."""
        raw = self._create_dsn_bounce()
        info = parser.parse(raw)

        assert info.original_message_id == "msg-test-001"

    def test_parse_dsn_with_enhanced_status(self, parser):
        """Parse enhanced status codes (X.Y.Z format)."""
        raw = self._create_dsn_bounce(status_code="5.2.2")
        info = parser.parse(raw)

        assert info.bounce_type == "hard"
        assert info.bounce_code is not None


class TestBounceParserHeuristic:
    """Tests for heuristic bounce detection."""

    @pytest.fixture
    def parser(self):
        return BounceParser()

    def _create_heuristic_bounce(
        self,
        subject: str = "Undelivered Mail Returned to Sender",
        from_addr: str = "mailer-daemon@test.local",
        body: str = "Your message could not be delivered. Error 550: User unknown.",
    ) -> bytes:
        """Create a non-standard bounce message."""
        msg = MIMEText(body, "plain")
        msg["From"] = from_addr
        msg["To"] = "sender@test.local"
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=True)
        return msg.as_bytes()

    def test_detect_bounce_by_subject(self, parser):
        """Detect bounce from subject pattern."""
        subjects = [
            "Mail Delivery Failed",
            "Undelivered Mail Returned to Sender",
            "Delivery Failure",
            "Message Undeliverable",
            "Returned mail: User unknown",
            "Failure Notice",
        ]
        for subject in subjects:
            raw = self._create_heuristic_bounce(subject=subject)
            info = parser.parse(raw)
            assert info.bounce_type is not None, f"Failed to detect bounce for: {subject}"

    def test_detect_bounce_by_from_address(self, parser):
        """Detect bounce from mailer-daemon sender."""
        from_addrs = [
            "MAILER-DAEMON@test.local",
            "postmaster@test.local",
            "mail-daemon@test.local",
        ]
        for from_addr in from_addrs:
            raw = self._create_heuristic_bounce(
                subject="Test message",
                from_addr=from_addr,
                body="Error 550: User unknown",
            )
            info = parser.parse(raw)
            assert info.bounce_type is not None, f"Failed for from: {from_addr}"

    def test_extract_smtp_code_from_body(self, parser):
        """Extract SMTP code from message body."""
        raw = self._create_heuristic_bounce(
            body="Delivery failed with error 550: User unknown at destination."
        )
        info = parser.parse(raw)

        assert info.bounce_code == "550"
        assert info.bounce_type == "hard"

    def test_extract_recipient_from_body(self, parser):
        """Extract recipient email from body."""
        raw = self._create_heuristic_bounce(
            body="Message to user@example.com was rejected: 550 User unknown"
        )
        info = parser.parse(raw)

        assert info.recipient == "user@example.com"

    def test_non_bounce_message(self, parser):
        """Regular message is not detected as bounce."""
        msg = MIMEText("Hello, this is a normal message.", "plain")
        msg["From"] = "friend@test.local"
        msg["To"] = "me@test.local"
        msg["Subject"] = "Hello there"

        info = parser.parse(msg.as_bytes())

        assert info.bounce_type is None
        assert info.original_message_id is None


class TestBounceInfo:
    """Tests for BounceInfo dataclass."""

    def test_bounce_info_fields(self):
        """BounceInfo has expected fields."""
        info = BounceInfo(
            original_message_id="msg-123",
            bounce_type="hard",
            bounce_code="550",
            bounce_reason="User unknown",
            recipient="user@test.com",
        )

        assert info.original_message_id == "msg-123"
        assert info.bounce_type == "hard"
        assert info.bounce_code == "550"
        assert info.bounce_reason == "User unknown"
        assert info.recipient == "user@test.com"

    def test_bounce_info_none_fields(self):
        """BounceInfo accepts None for all fields."""
        info = BounceInfo(
            original_message_id=None,
            bounce_type=None,
            bounce_code=None,
            bounce_reason=None,
            recipient=None,
        )

        assert info.original_message_id is None
        assert info.bounce_type is None


class TestBounceCodeClassification:
    """Tests for bounce code classification."""

    @pytest.fixture
    def parser(self):
        return BounceParser()

    def test_hard_bounce_codes(self, parser):
        """5xx codes are classified as hard bounces."""
        hard_codes = ["500", "550", "551", "552", "553", "554"]
        for code in hard_codes:
            assert code in parser.HARD_BOUNCE_CODES

    def test_soft_bounce_codes(self, parser):
        """4xx codes are classified as soft bounces."""
        soft_codes = ["400", "421", "450", "451", "452"]
        for code in soft_codes:
            assert code in parser.SOFT_BOUNCE_CODES

    def test_code_patterns(self, parser):
        """Regex patterns match expected formats."""
        # Simple code
        assert parser.SMTP_CODE_PATTERN.search("Error 550 occurred")
        assert parser.SMTP_CODE_PATTERN.search("421 Try again later")

        # Enhanced code
        assert parser.ENHANCED_CODE_PATTERN.search("Status: 5.1.1")
        assert parser.ENHANCED_CODE_PATTERN.search("4.2.2 Mailbox full")


class TestBounceParserEdgeCases:
    """Tests for edge cases and additional coverage."""

    @pytest.fixture
    def parser(self):
        return BounceParser()

    def test_dsn_with_simple_status_code(self, parser):
        """Parse DSN with simple 3-digit status code (not enhanced)."""
        msg = MIMEMultipart("report", report_type="delivery-status")
        msg["From"] = "mailer-daemon@test.local"
        msg["Subject"] = "Delivery Failure"

        human = MIMEText("Delivery failed", "plain")
        msg.attach(human)

        # Status with simple code only (no X.Y.Z format)
        dsn_text = """Final-Recipient: rfc822; user@test.com
Action: failed
Status: 550
"""
        dsn = MIMEText(dsn_text, "delivery-status")
        msg.attach(dsn)

        info = parser.parse(msg.as_bytes())
        assert info.bounce_code == "550"
        assert info.bounce_type == "hard"

    def test_dsn_with_action_delayed(self, parser):
        """Parse DSN with action=delayed (soft bounce)."""
        msg = MIMEMultipart("report", report_type="delivery-status")
        msg["From"] = "mailer-daemon@test.local"
        msg["Subject"] = "Delivery Delayed"

        human = MIMEText("Delivery delayed", "plain")
        msg.attach(human)

        dsn_text = """Final-Recipient: rfc822; user@test.com
Action: delayed
"""
        dsn = MIMEText(dsn_text, "delivery-status")
        msg.attach(dsn)

        info = parser.parse(msg.as_bytes())
        assert info.bounce_type == "soft"

    def test_dsn_with_diagnostic_code_extraction(self, parser):
        """Extract bounce code from Diagnostic-Code when Status missing."""
        msg = MIMEMultipart("report", report_type="delivery-status")
        msg["From"] = "mailer-daemon@test.local"
        msg["Subject"] = "Delivery Failure"

        human = MIMEText("Delivery failed", "plain")
        msg.attach(human)

        # No Status field, only Diagnostic-Code
        dsn_text = """Final-Recipient: rfc822; user@test.com
Action: failed
Diagnostic-Code: smtp; 551 User not local
"""
        dsn = MIMEText(dsn_text, "delivery-status")
        msg.attach(dsn)

        info = parser.parse(msg.as_bytes())
        assert info.bounce_code == "551"
        assert info.bounce_type == "hard"
        assert "User not local" in info.bounce_reason

    def test_dsn_with_original_message_rfc822(self, parser):
        """Extract original ID from message/rfc822 attachment."""
        from email.mime.message import MIMEMessage

        msg = MIMEMultipart("report", report_type="delivery-status")
        msg["From"] = "mailer-daemon@test.local"
        msg["Subject"] = "Delivery Failure"

        human = MIMEText("Delivery failed", "plain")
        msg.attach(human)

        dsn_text = """Final-Recipient: rfc822; user@test.com
Action: failed
Status: 5.1.1
"""
        dsn = MIMEText(dsn_text, "delivery-status")
        msg.attach(dsn)

        # Original message as message/rfc822
        original = MIMEText("Original body", "plain")
        original["Message-ID"] = "<original-123@test.com>"
        original["X-Genro-Mail-ID"] = "genro-msg-456"
        original["From"] = "sender@test.com"
        original["To"] = "user@test.com"
        wrapped = MIMEMessage(original)
        msg.attach(wrapped)

        info = parser.parse(msg.as_bytes())
        assert info.original_message_id == "genro-msg-456"

    def test_heuristic_with_maildelivery_sender(self, parser):
        """Detect bounce from maildelivery sender."""
        msg = MIMEText("Error 550: User unknown", "plain")
        msg["From"] = "maildelivery@test.local"
        msg["To"] = "sender@test.local"
        msg["Subject"] = "Some notification"

        info = parser.parse(msg.as_bytes())
        assert info.bounce_type is not None

    def test_heuristic_soft_bounce_code(self, parser):
        """Heuristic parsing with 4xx code."""
        msg = MIMEText("Temporary failure: 421 Try again later", "plain")
        msg["From"] = "mailer-daemon@test.local"
        msg["To"] = "sender@test.local"
        msg["Subject"] = "Delivery Status"

        info = parser.parse(msg.as_bytes())
        assert info.bounce_code == "421"
        assert info.bounce_type == "soft"

    def test_heuristic_no_smtp_code(self, parser):
        """Heuristic bounce without SMTP code defaults to hard."""
        msg = MIMEText("Your message could not be delivered.", "plain")
        msg["From"] = "mailer-daemon@test.local"
        msg["To"] = "sender@test.local"
        msg["Subject"] = "Delivery Failure"

        info = parser.parse(msg.as_bytes())
        # Even without a code, detected as bounce defaults to hard
        assert info.bounce_type == "hard"

    def test_find_original_id_in_body(self, parser):
        """Find X-Genro-Mail-ID in message body text."""
        body = """Your message could not be delivered.

Original message headers:
X-Genro-Mail-ID: genro-body-789
From: sender@test.com
To: user@test.com
"""
        msg = MIMEText(body, "plain")
        msg["From"] = "mailer-daemon@test.local"
        msg["To"] = "sender@test.local"
        msg["Subject"] = "Delivery Failure"

        info = parser.parse(msg.as_bytes())
        assert info.original_message_id == "genro-body-789"

    def test_find_original_id_in_header(self, parser):
        """Find X-Genro-Mail-ID in message headers directly."""
        msg = MIMEText("Bounce message", "plain")
        msg["From"] = "mailer-daemon@test.local"
        msg["To"] = "sender@test.local"
        msg["Subject"] = "Delivery Failure"
        msg["X-Genro-Mail-ID"] = "genro-header-101"

        info = parser.parse(msg.as_bytes())
        assert info.original_message_id == "genro-header-101"

    def test_multipart_text_body_extraction(self, parser):
        """Extract text body from multipart message."""
        msg = MIMEMultipart("alternative")
        msg["From"] = "mailer-daemon@test.local"
        msg["To"] = "sender@test.local"
        msg["Subject"] = "Delivery Failure"

        text = MIMEText("Error 553: User not found at user@test.com", "plain")
        html = MIMEText("<p>Error</p>", "html")
        msg.attach(text)
        msg.attach(html)

        info = parser.parse(msg.as_bytes())
        assert info.bounce_code == "553"
        assert info.recipient == "user@test.com"

    def test_dsn_report_type_not_delivery_status(self, parser):
        """Multipart/report with non-delivery-status type uses heuristic."""
        msg = MIMEMultipart("report", report_type="disposition-notification")
        msg["From"] = "mailer-daemon@test.local"
        msg["To"] = "sender@test.local"
        msg["Subject"] = "Delivery Failure"

        body = MIMEText("Error 550: User unknown", "plain")
        msg.attach(body)

        info = parser.parse(msg.as_bytes())
        # Should fall back to heuristic parsing
        assert info.bounce_code == "550"

    def test_dsn_with_list_payload_status(self, parser):
        """Parse DSN where delivery-status has list payload (per-recipient)."""
        # Create raw DSN with list-style delivery-status
        # This simulates MTAs that return multiple per-recipient status blocks
        raw_dsn = b"""MIME-Version: 1.0
Content-Type: multipart/report; report-type=delivery-status; boundary="BOUNDARY"
From: mailer-daemon@test.local
Subject: Delivery Status

--BOUNDARY
Content-Type: text/plain

Delivery failed for multiple recipients.

--BOUNDARY
Content-Type: message/delivery-status

Reporting-MTA: dns; test.local

Final-Recipient: rfc822; first@test.com
Action: failed
Status: 5.1.1
Diagnostic-Code: smtp; 550 User unknown

Final-Recipient: rfc822; second@test.com
Action: failed
Status: 5.1.2

--BOUNDARY--
"""
        info = parser.parse(raw_dsn)
        # Should extract info from at least one recipient
        assert info.bounce_type == "hard"
        assert info.recipient is not None

    def test_extract_original_id_returns_none_for_empty(self, parser):
        """_extract_original_id returns None when no ID found."""
        from email.mime.message import MIMEMessage

        msg = MIMEMultipart("report", report_type="delivery-status")
        msg["From"] = "mailer-daemon@test.local"
        msg["Subject"] = "Delivery Failure"

        human = MIMEText("Failed", "plain")
        msg.attach(human)

        dsn = MIMEText("Final-Recipient: rfc822; user@test.com\nAction: failed\nStatus: 5.1.1", "delivery-status")
        msg.attach(dsn)

        # Original message without X-Genro-Mail-ID
        original = MIMEText("Body", "plain")
        original["From"] = "sender@test.com"
        wrapped = MIMEMessage(original)
        msg.attach(wrapped)

        info = parser.parse(msg.as_bytes())
        # Original ID should be None when not present
        assert info.original_message_id is None

    def test_get_text_body_non_multipart_string_payload(self, parser):
        """_get_text_body handles non-bytes payload."""
        # Create a message where get_payload(decode=True) might return non-bytes
        raw = b"""From: mailer-daemon@test.local
To: sender@test.local
Subject: Delivery Failure
Content-Type: text/plain

Error 554: Rejected
"""
        info = parser.parse(raw)
        assert info.bounce_code == "554"

    def test_get_text_body_multipart_no_text_plain(self, parser):
        """_get_text_body returns empty for multipart without text/plain."""
        msg = MIMEMultipart("mixed")
        msg["From"] = "mailer-daemon@test.local"
        msg["To"] = "sender@test.local"
        msg["Subject"] = "Delivery Failure"

        # Only HTML part, no text/plain
        html = MIMEText("<p>Error 550</p>", "html")
        msg.attach(html)

        info = parser.parse(msg.as_bytes())
        # Won't detect bounce without text body to parse codes from
        # But may still detect by subject/from
        assert info.bounce_type is not None  # Detected by subject/from

    def test_heuristic_empty_body(self, parser):
        """Heuristic parsing with empty body."""
        msg = MIMEText("", "plain")
        msg["From"] = "mailer-daemon@test.local"
        msg["To"] = "sender@test.local"
        msg["Subject"] = "Delivery Failure"

        info = parser.parse(msg.as_bytes())
        # Detected as bounce but no code extracted
        assert info.bounce_type == "hard"  # Default
        assert info.bounce_code is None
