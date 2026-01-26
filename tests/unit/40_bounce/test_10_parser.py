# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""Unit tests for BounceParser."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from email.message import Message
from email.mime.message import MIMEMessage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import pytest

from mail_proxy.bounce import BounceInfo, BounceParser


def create_dsn_bounce_email(
    original_message_id: str,
    recipient: str = "failed@example.com",
    bounce_code: str = "550",
    bounce_reason: str = "User unknown",
) -> bytes:
    """Create a DSN (RFC 3464) formatted bounce email."""
    msg = MIMEMultipart("report", report_type="delivery-status")
    msg["From"] = "MAILER-DAEMON@mail.example.com"
    msg["To"] = "bounces@localhost"
    msg["Subject"] = "Undelivered Mail Returned to Sender"
    msg["Message-ID"] = f"<bounce-{uuid.uuid4()}@mail.example.com>"

    # Part 1: Human-readable explanation
    explanation = MIMEText(
        f"This is the mail system at mail.example.com.\n\n"
        f"I'm sorry to have to inform you that your message could not\n"
        f"be delivered to one or more recipients.\n\n"
        f"<{recipient}>: delivery failed\n"
        f"    {bounce_code} {bounce_reason}\n",
        "plain"
    )
    msg.attach(explanation)

    # Part 2: Delivery status (machine-readable)
    dsn_text = (
        f"Reporting-MTA: dns; mail.example.com\n"
        f"Arrival-Date: {datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S %z')}\n"
        f"\n"
        f"Final-Recipient: rfc822; {recipient}\n"
        f"Action: failed\n"
        f"Status: {bounce_code[0]}.{bounce_code[1]}.{bounce_code[2]}\n"
        f"Diagnostic-Code: smtp; {bounce_code} {bounce_reason}\n"
    )
    dsn_part = MIMEText(dsn_text, "delivery-status")
    msg.attach(dsn_part)

    # Part 3: Original message headers
    original_headers = Message()
    original_headers["X-Genro-Mail-ID"] = original_message_id
    original_headers["From"] = "sender@test.com"
    original_headers["To"] = recipient
    original_headers["Subject"] = "Original Test Message"
    original_headers["Message-ID"] = f"<original-{original_message_id}@test.com>"

    original_part = MIMEMessage(original_headers, "rfc822-headers")
    msg.attach(original_part)

    return msg.as_bytes()


def create_simple_bounce_email(
    from_addr: str = "MAILER-DAEMON@example.com",
    subject: str = "Undelivered Mail Returned to Sender",
    body: str = "550 User unknown",
) -> bytes:
    """Create a simple non-DSN bounce email for heuristic parsing."""
    msg = MIMEText(body, "plain")
    msg["From"] = from_addr
    msg["To"] = "sender@test.com"
    msg["Subject"] = subject
    msg["Message-ID"] = f"<bounce-{uuid.uuid4()}@example.com>"
    return msg.as_bytes()


class TestBounceInfo:
    """Tests for BounceInfo dataclass."""

    def test_bounce_info_fields(self):
        """BounceInfo has all expected fields."""
        info = BounceInfo(
            original_message_id="msg-123",
            bounce_type="hard",
            bounce_code="550",
            bounce_reason="User unknown",
            recipient="user@example.com",
        )
        assert info.original_message_id == "msg-123"
        assert info.bounce_type == "hard"
        assert info.bounce_code == "550"
        assert info.bounce_reason == "User unknown"
        assert info.recipient == "user@example.com"

    def test_bounce_info_nullable_fields(self):
        """BounceInfo fields can be None."""
        info = BounceInfo(
            original_message_id=None,
            bounce_type=None,
            bounce_code=None,
            bounce_reason=None,
            recipient=None,
        )
        assert info.original_message_id is None
        assert info.bounce_type is None


class TestBounceParserDSN:
    """Tests for DSN (RFC 3464) parsing."""

    def test_parse_hard_bounce_550(self):
        """Parse a hard bounce with 550 code."""
        parser = BounceParser()
        raw = create_dsn_bounce_email(
            original_message_id="test-msg-550",
            recipient="invalid@example.com",
            bounce_code="550",
            bounce_reason="User unknown",
        )
        info = parser.parse(raw)

        assert info.original_message_id == "test-msg-550"
        assert info.bounce_type == "hard"
        assert info.bounce_code is not None
        assert "5" in info.bounce_code
        assert info.recipient == "invalid@example.com"

    def test_parse_hard_bounce_551(self):
        """Parse a hard bounce with 551 code."""
        parser = BounceParser()
        raw = create_dsn_bounce_email(
            original_message_id="test-msg-551",
            bounce_code="551",
            bounce_reason="User not local",
        )
        info = parser.parse(raw)

        assert info.bounce_type == "hard"
        assert "5" in (info.bounce_code or "")

    def test_parse_hard_bounce_552(self):
        """Parse a hard bounce with 552 code (mailbox full - permanent)."""
        parser = BounceParser()
        raw = create_dsn_bounce_email(
            original_message_id="test-msg-552",
            bounce_code="552",
            bounce_reason="Mailbox full",
        )
        info = parser.parse(raw)

        assert info.bounce_type == "hard"

    def test_parse_hard_bounce_553(self):
        """Parse a hard bounce with 553 code."""
        parser = BounceParser()
        raw = create_dsn_bounce_email(
            original_message_id="test-msg-553",
            bounce_code="553",
            bounce_reason="Mailbox name invalid",
        )
        info = parser.parse(raw)

        assert info.bounce_type == "hard"

    def test_parse_hard_bounce_554(self):
        """Parse a hard bounce with 554 code."""
        parser = BounceParser()
        raw = create_dsn_bounce_email(
            original_message_id="test-msg-554",
            bounce_code="554",
            bounce_reason="Transaction failed",
        )
        info = parser.parse(raw)

        assert info.bounce_type == "hard"

    def test_parse_soft_bounce_421(self):
        """Parse a soft bounce with 421 code."""
        parser = BounceParser()
        raw = create_dsn_bounce_email(
            original_message_id="test-msg-421",
            bounce_code="421",
            bounce_reason="Service not available",
        )
        info = parser.parse(raw)

        assert info.original_message_id == "test-msg-421"
        assert info.bounce_type == "soft"

    def test_parse_soft_bounce_450(self):
        """Parse a soft bounce with 450 code."""
        parser = BounceParser()
        raw = create_dsn_bounce_email(
            original_message_id="test-msg-450",
            bounce_code="450",
            bounce_reason="Mailbox unavailable",
        )
        info = parser.parse(raw)

        assert info.bounce_type == "soft"

    def test_parse_soft_bounce_451(self):
        """Parse a soft bounce with 451 code."""
        parser = BounceParser()
        raw = create_dsn_bounce_email(
            original_message_id="test-msg-451",
            bounce_code="451",
            bounce_reason="Local error in processing",
        )
        info = parser.parse(raw)

        assert info.bounce_type == "soft"

    def test_parse_soft_bounce_452(self):
        """Parse a soft bounce with 452 code."""
        parser = BounceParser()
        raw = create_dsn_bounce_email(
            original_message_id="test-msg-452",
            bounce_code="452",
            bounce_reason="Insufficient storage",
        )
        info = parser.parse(raw)

        assert info.bounce_type == "soft"

    def test_extracts_recipient(self):
        """Parser extracts recipient from Final-Recipient field."""
        parser = BounceParser()
        raw = create_dsn_bounce_email(
            original_message_id="test-recipient",
            recipient="specific-user@domain.com",
            bounce_code="550",
        )
        info = parser.parse(raw)

        assert info.recipient == "specific-user@domain.com"

    def test_extracts_diagnostic_reason(self):
        """Parser extracts reason from Diagnostic-Code field."""
        parser = BounceParser()
        raw = create_dsn_bounce_email(
            original_message_id="test-reason",
            bounce_code="550",
            bounce_reason="The email account does not exist",
        )
        info = parser.parse(raw)

        assert info.bounce_reason is not None
        assert "exist" in info.bounce_reason.lower() or "550" in info.bounce_reason

    def test_extracts_original_message_id(self):
        """Parser extracts X-Genro-Mail-ID from original headers."""
        parser = BounceParser()
        raw = create_dsn_bounce_email(
            original_message_id="unique-tracking-id-12345",
            bounce_code="550",
        )
        info = parser.parse(raw)

        assert info.original_message_id == "unique-tracking-id-12345"

    def test_multiple_bounces_unique_ids(self):
        """Multiple bounces correlate to correct original messages."""
        parser = BounceParser()

        for i in range(5):
            raw = create_dsn_bounce_email(
                original_message_id=f"msg-batch-{i}",
                recipient=f"user{i}@example.com",
                bounce_code="550",
            )
            info = parser.parse(raw)
            assert info.original_message_id == f"msg-batch-{i}"


class TestBounceParserHeuristic:
    """Tests for heuristic (non-DSN) bounce parsing."""

    def test_detect_bounce_by_subject_delivery_failure(self):
        """Detect bounce from 'delivery failure' subject."""
        parser = BounceParser()
        raw = create_simple_bounce_email(
            subject="Mail Delivery Failure",
            body="550 User not found\nuser@example.com",
        )
        info = parser.parse(raw)

        assert info.bounce_type is not None

    def test_detect_bounce_by_subject_undelivered(self):
        """Detect bounce from 'undelivered mail' subject."""
        parser = BounceParser()
        raw = create_simple_bounce_email(
            subject="Undelivered Mail Returned to Sender",
            body="421 Try again later",
        )
        info = parser.parse(raw)

        assert info.bounce_type is not None

    def test_detect_bounce_by_subject_returned(self):
        """Detect bounce from 'returned mail' subject."""
        parser = BounceParser()
        raw = create_simple_bounce_email(
            subject="Returned mail: see transcript",
            body="550 Unknown user",
        )
        info = parser.parse(raw)

        assert info.bounce_type is not None

    def test_detect_bounce_by_subject_failure_notice(self):
        """Detect bounce from 'failure notice' subject."""
        parser = BounceParser()
        raw = create_simple_bounce_email(
            subject="Failure Notice",
            body="Sorry, we were unable to deliver your message.",
        )
        info = parser.parse(raw)

        assert info.bounce_type is not None

    def test_detect_bounce_by_from_mailer_daemon(self):
        """Detect bounce from MAILER-DAEMON sender."""
        parser = BounceParser()
        raw = create_simple_bounce_email(
            from_addr="MAILER-DAEMON@mail.example.com",
            subject="Notification",
            body="550 Recipient rejected",
        )
        info = parser.parse(raw)

        assert info.bounce_type is not None

    def test_detect_bounce_by_from_postmaster(self):
        """Detect bounce from postmaster sender."""
        parser = BounceParser()
        raw = create_simple_bounce_email(
            from_addr="postmaster@mail.example.com",
            subject="Message status",
            body="Your message could not be delivered. 550 No such user.",
        )
        info = parser.parse(raw)

        assert info.bounce_type is not None

    def test_extract_code_from_body(self):
        """Extract SMTP code from email body."""
        parser = BounceParser()
        raw = create_simple_bounce_email(
            subject="Mail Delivery Failed",
            body="The following error occurred: 550 5.1.1 User unknown",
        )
        info = parser.parse(raw)

        assert info.bounce_code is not None
        assert "550" in info.bounce_code or "5" in info.bounce_code

    def test_extract_recipient_from_body(self):
        """Extract recipient email from body."""
        parser = BounceParser()
        raw = create_simple_bounce_email(
            subject="Delivery Status Notification",
            body="Delivery to recipient@domain.org failed permanently.\n550 User unknown",
        )
        info = parser.parse(raw)

        assert info.recipient == "recipient@domain.org"

    def test_non_bounce_email_returns_empty(self):
        """Non-bounce email returns empty BounceInfo."""
        parser = BounceParser()
        msg = MIMEText("Hello, this is a normal email.", "plain")
        msg["From"] = "friend@example.com"
        msg["To"] = "me@example.com"
        msg["Subject"] = "Hello there!"

        info = parser.parse(msg.as_bytes())

        assert info.original_message_id is None
        assert info.bounce_type is None
        assert info.bounce_code is None

    def test_newsletter_not_detected_as_bounce(self):
        """Newsletter email not detected as bounce."""
        parser = BounceParser()
        msg = MIMEText("Check out our latest products!", "plain")
        msg["From"] = "newsletter@shop.example.com"
        msg["To"] = "customer@example.com"
        msg["Subject"] = "Weekly Newsletter - Great Deals!"

        info = parser.parse(msg.as_bytes())

        assert info.bounce_type is None


class TestBounceParserEdgeCases:
    """Tests for edge cases and error handling."""

    def test_empty_email(self):
        """Handle empty email gracefully."""
        parser = BounceParser()
        raw = b""
        info = parser.parse(raw)

        assert info.original_message_id is None

    def test_malformed_email(self):
        """Handle malformed email gracefully."""
        parser = BounceParser()
        raw = b"This is not a valid email format\n\nJust random text."
        info = parser.parse(raw)

        assert info.original_message_id is None

    def test_truncated_dsn(self):
        """Handle truncated DSN message."""
        parser = BounceParser()
        # Create partial DSN
        msg = MIMEMultipart("report", report_type="delivery-status")
        msg["From"] = "MAILER-DAEMON@example.com"
        msg["Subject"] = "Delivery failure"
        # No delivery-status part attached

        info = parser.parse(msg.as_bytes())
        # Should not crash
        assert info is not None

    def test_very_long_reason_truncated(self):
        """Very long bounce reason is truncated."""
        parser = BounceParser()
        long_reason = "Error: " + "x" * 1000
        raw = create_dsn_bounce_email(
            original_message_id="test-long",
            bounce_code="550",
            bounce_reason=long_reason,
        )
        info = parser.parse(raw)

        if info.bounce_reason:
            assert len(info.bounce_reason) <= 500

    def test_unicode_in_bounce_reason(self):
        """Handle unicode characters in bounce reason without crashing."""
        parser = BounceParser()
        raw = create_dsn_bounce_email(
            original_message_id="test-unicode",
            bounce_code="550",
            bounce_reason="Utente sconosciuto: 用户不存在",
        )
        # Should not raise an exception
        info = parser.parse(raw)

        # Original message ID should still be extracted
        assert info.original_message_id == "test-unicode"

    def test_enhanced_status_code_parsing(self):
        """Parse enhanced status codes like 5.1.1."""
        parser = BounceParser()
        # The DSN helper creates Status: 5.5.0 format
        raw = create_dsn_bounce_email(
            original_message_id="test-enhanced",
            bounce_code="550",
        )
        info = parser.parse(raw)

        assert info.bounce_type == "hard"


class TestBounceParserConstants:
    """Tests for parser constants."""

    def test_hard_bounce_codes_are_5xx(self):
        """All hard bounce codes start with 5."""
        for code in BounceParser.HARD_BOUNCE_CODES:
            assert code.startswith("5"), f"Hard bounce code {code} should start with 5"

    def test_soft_bounce_codes_are_4xx(self):
        """All soft bounce codes start with 4."""
        for code in BounceParser.SOFT_BOUNCE_CODES:
            assert code.startswith("4"), f"Soft bounce code {code} should start with 4"

    def test_common_hard_codes_included(self):
        """Common hard bounce codes are included."""
        common_hard = ["550", "551", "552", "553", "554"]
        for code in common_hard:
            assert code in BounceParser.HARD_BOUNCE_CODES

    def test_common_soft_codes_included(self):
        """Common soft bounce codes are included."""
        common_soft = ["421", "450", "451", "452"]
        for code in common_soft:
            assert code in BounceParser.SOFT_BOUNCE_CODES
