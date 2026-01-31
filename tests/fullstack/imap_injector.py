# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""IMAP Injector: inject bounce messages into IMAP mailbox for testing.

This module allows tests to simulate bounce emails by appending
RFC-compliant bounce messages directly to an IMAP mailbox.
"""

from __future__ import annotations

import imaplib
from email.message import EmailMessage
from email.utils import formatdate
from typing import Any


class IMAPBounceInjector:
    """Inject bounce messages into IMAP mailbox for testing."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 1143,
        user: str = "test",
        password: str = "test",
    ):
        """Initialize IMAP connection parameters.

        Args:
            host: IMAP server host.
            port: IMAP server port.
            user: IMAP username (Mailpit accepts any).
            password: IMAP password (Mailpit accepts any).
        """
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self._conn: imaplib.IMAP4 | None = None

    def connect(self) -> None:
        """Connect to IMAP server."""
        self._conn = imaplib.IMAP4(self.host, self.port)
        self._conn.login(self.user, self.password)

    def disconnect(self) -> None:
        """Disconnect from IMAP server."""
        if self._conn:
            try:
                self._conn.logout()
            except Exception:
                pass
            self._conn = None

    def __enter__(self) -> "IMAPBounceInjector":
        self.connect()
        return self

    def __exit__(self, *args: Any) -> None:
        self.disconnect()

    def inject_bounce(
        self,
        original_message_id: str,
        original_from: str,
        original_to: str,
        bounce_reason: str = "User unknown",
        bounce_code: str = "550",
        return_path: str = "mailer-daemon@test.local",
    ) -> None:
        """Inject a bounce message for an original message.

        Creates an RFC 3464 compliant Delivery Status Notification (DSN).

        Args:
            original_message_id: Message-ID of the original failed message.
            original_from: Original sender address.
            original_to: Original recipient that bounced.
            bounce_reason: Human-readable bounce reason.
            bounce_code: SMTP error code (e.g., "550", "552").
            return_path: Return-Path address for the bounce.
        """
        if not self._conn:
            raise RuntimeError("Not connected to IMAP server")

        bounce_msg = self._create_bounce_message(
            original_message_id=original_message_id,
            original_from=original_from,
            original_to=original_to,
            bounce_reason=bounce_reason,
            bounce_code=bounce_code,
            return_path=return_path,
        )

        # Select INBOX and append the bounce message
        self._conn.select("INBOX")
        self._conn.append(
            "INBOX",
            None,  # No flags
            None,  # Current time
            bounce_msg.as_bytes(),
        )

    def _create_bounce_message(
        self,
        original_message_id: str,
        original_from: str,
        original_to: str,
        bounce_reason: str,
        bounce_code: str,
        return_path: str,
    ) -> EmailMessage:
        """Create an RFC 3464 DSN bounce message.

        The bounce message is a multipart/report with:
        - text/plain: Human-readable explanation
        - message/delivery-status: Machine-readable status
        - message/rfc822 (optional): Original message headers
        """
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        # Create multipart/report message
        msg = MIMEMultipart("report", report_type="delivery-status")
        msg["From"] = return_path
        msg["To"] = original_from
        msg["Subject"] = f"Undelivered Mail Returned to Sender"
        msg["Date"] = formatdate(localtime=True)
        msg["Auto-Submitted"] = "auto-replied"

        # Part 1: Human-readable explanation
        human_text = f"""This is the mail system at host test.local.

I'm sorry to have to inform you that your message could not
be delivered to one or more recipients.

<{original_to}>: {bounce_reason}

For further assistance, please contact your mail administrator.
"""
        part1 = MIMEText(human_text, "plain")
        msg.attach(part1)

        # Part 2: Machine-readable delivery status (RFC 3464)
        dsn_text = f"""Reporting-MTA: dns; test.local
Arrival-Date: {formatdate(localtime=True)}

Final-Recipient: rfc822; {original_to}
Action: failed
Status: {bounce_code[0]}.0.0
Diagnostic-Code: smtp; {bounce_code} {bounce_reason}
"""
        # delivery-status is a special MIME type
        part2 = MIMEText(dsn_text, "delivery-status")
        msg.attach(part2)

        # Part 3: Original message headers (simplified)
        original_headers = f"""Message-ID: {original_message_id}
From: {original_from}
To: {original_to}
Subject: [Original subject not available]
Date: {formatdate(localtime=True)}
"""
        part3 = MIMEText(original_headers, "rfc822-headers")
        msg.attach(part3)

        return msg

    def inject_hard_bounce(
        self,
        original_message_id: str,
        original_from: str,
        original_to: str,
    ) -> None:
        """Inject a hard bounce (permanent failure, user unknown)."""
        self.inject_bounce(
            original_message_id=original_message_id,
            original_from=original_from,
            original_to=original_to,
            bounce_reason="User unknown",
            bounce_code="550",
        )

    def inject_soft_bounce(
        self,
        original_message_id: str,
        original_from: str,
        original_to: str,
    ) -> None:
        """Inject a soft bounce (temporary failure, mailbox full)."""
        self.inject_bounce(
            original_message_id=original_message_id,
            original_from=original_from,
            original_to=original_to,
            bounce_reason="Mailbox full",
            bounce_code="452",
        )

    def inject_quota_bounce(
        self,
        original_message_id: str,
        original_from: str,
        original_to: str,
    ) -> None:
        """Inject a quota exceeded bounce."""
        self.inject_bounce(
            original_message_id=original_message_id,
            original_from=original_from,
            original_to=original_to,
            bounce_reason="Quota exceeded",
            bounce_code="552",
        )


__all__ = ["IMAPBounceInjector"]
