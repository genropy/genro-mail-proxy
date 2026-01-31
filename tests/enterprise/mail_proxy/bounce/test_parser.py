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
