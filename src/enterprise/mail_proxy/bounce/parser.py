# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: BSL-1.1
"""Bounce message parser for DSN (RFC 3464) and heuristic detection."""

from __future__ import annotations

import email
import re
from dataclasses import dataclass
from email.message import Message
from typing import Literal


@dataclass
class BounceInfo:
    """Parsed bounce information."""

    original_message_id: str | None  # X-Genro-Mail-ID from original message
    bounce_type: Literal["hard", "soft"] | None
    bounce_code: str | None  # SMTP code e.g. "550", "421"
    bounce_reason: str | None
    recipient: str | None


class BounceParser:
    """Parse bounce messages (DSN RFC 3464 + heuristics)."""

    # Hard bounce codes (5xx permanent failures)
    HARD_BOUNCE_CODES = frozenset(
        [
            "500",
            "501",
            "502",
            "503",
            "504",
            "510",
            "511",
            "512",
            "513",
            "521",
            "522",
            "523",
            "530",
            "531",
            "532",
            "541",
            "542",
            "543",
            "550",
            "551",
            "552",
            "553",
            "554",
            "555",
            "556",
            "557",
        ]
    )

    # Soft bounce codes (4xx temporary failures)
    SOFT_BOUNCE_CODES = frozenset(
        [
            "400",
            "401",
            "402",
            "403",
            "404",
            "405",
            "407",
            "408",
            "409",
            "410",
            "411",
            "412",
            "413",
            "414",
            "421",
            "422",
            "431",
            "432",
            "441",
            "442",
            "450",
            "451",
            "452",
            "453",
            "454",
            "455",
            "456",
            "471",
            "472",
        ]
    )

    # Patterns for extracting bounce info from non-standard bounces
    BOUNCE_SUBJECT_PATTERNS = [
        re.compile(
            r"(?:mail|message|delivery)\s*(?:delivery|failure|failed|returned|undeliverable)", re.I
        ),
        re.compile(r"undelivered\s*mail\s*returned", re.I),
        re.compile(r"(?:returned|bounced)\s*mail", re.I),
        re.compile(r"failure\s*notice", re.I),
    ]

    SMTP_CODE_PATTERN = re.compile(r"\b([45]\d{2})\b")
    ENHANCED_CODE_PATTERN = re.compile(r"\b([45])\.(\d+)\.(\d+)\b")

    def parse(self, raw_email: bytes) -> BounceInfo:
        """Parse a bounce message and extract bounce information."""
        msg = email.message_from_bytes(raw_email)
        content_type = msg.get_content_type()

        # Check if this is a DSN (RFC 3464)
        if content_type == "multipart/report":
            report_type = msg.get_param("report-type") or ""
            if isinstance(report_type, str) and report_type.lower() == "delivery-status":
                return self._parse_dsn(msg)

        # Fallback: heuristic parsing
        return self._parse_heuristic(msg)

    def _parse_dsn(self, msg: Message) -> BounceInfo:
        """Parse a standard DSN (RFC 3464) bounce message."""
        original_id: str | None = None
        bounce_type: Literal["hard", "soft"] | None = None
        bounce_code: str | None = None
        bounce_reason: str | None = None
        recipient: str | None = None

        for part in msg.walk():
            part_type = part.get_content_type()

            # Parse delivery-status part (RFC 3464 uses message/delivery-status,
            # but some MTAs use text/delivery-status - accept both)
            if part_type in ("message/delivery-status", "text/delivery-status"):
                payload = part.get_payload()
                if isinstance(payload, list):
                    for status_part in payload:
                        status_text = str(status_part)
                        r, c, t, m = self._extract_dsn_info(status_text)
                        recipient = r or recipient
                        bounce_code = c or bounce_code
                        bounce_type = t or bounce_type
                        bounce_reason = m or bounce_reason
                elif isinstance(payload, str):
                    recipient, bounce_code, bounce_type, bounce_reason = self._extract_dsn_info(
                        payload
                    )

            # Extract original message ID from attached original message
            # RFC 3464 uses message/rfc822 for full message or text/rfc822-headers for headers only
            # MIMEMessage with "rfc822-headers" produces message/rfc822-headers - accept all variants
            elif part_type in ("message/rfc822", "message/rfc822-headers", "text/rfc822-headers"):
                original_id = self._extract_original_id(part)

        return BounceInfo(
            original_message_id=original_id,
            bounce_type=bounce_type,
            bounce_code=bounce_code,
            bounce_reason=bounce_reason,
            recipient=recipient,
        )

    def _extract_dsn_info(
        self, status_text: str
    ) -> tuple[str | None, str | None, Literal["hard", "soft"] | None, str | None]:
        """Extract bounce info from DSN status text."""
        recipient: str | None = None
        bounce_code: str | None = None
        bounce_type: Literal["hard", "soft"] | None = None
        bounce_reason: str | None = None

        for line in status_text.split("\n"):
            line = line.strip()
            lower_line = line.lower()

            if lower_line.startswith("final-recipient:"):
                # Format: Final-Recipient: rfc822; user@example.com
                parts = line.split(";", 1)
                if len(parts) > 1:
                    recipient = parts[1].strip()

            elif lower_line.startswith("status:"):
                # Format: Status: 5.1.1 or Status: 550
                status = line.split(":", 1)[1].strip()
                # Try enhanced status code first
                match = self.ENHANCED_CODE_PATTERN.search(status)
                if match:
                    class_digit = match.group(1)
                    bounce_code = f"{class_digit}{match.group(2)}{match.group(3)}"
                    bounce_type = "hard" if class_digit == "5" else "soft"
                else:
                    # Try simple code
                    match = self.SMTP_CODE_PATTERN.search(status)
                    if match:
                        bounce_code = match.group(1)
                        bounce_type = "hard" if bounce_code.startswith("5") else "soft"

            elif lower_line.startswith("diagnostic-code:"):
                # Format: Diagnostic-Code: smtp; 550 User unknown
                diag = line.split(":", 1)[1].strip()
                if ";" in diag:
                    diag = diag.split(";", 1)[1].strip()
                bounce_reason = diag[:500]  # Limit length

                # Extract code from diagnostic if not found yet
                if not bounce_code:
                    match = self.SMTP_CODE_PATTERN.search(diag)
                    if match:
                        bounce_code = match.group(1)
                        bounce_type = "hard" if bounce_code.startswith("5") else "soft"

            elif lower_line.startswith("action:"):
                action = line.split(":", 1)[1].strip().lower()
                if action == "failed" and bounce_type is None:
                    bounce_type = "hard"
                elif action in ("delayed", "relayed", "expanded"):
                    bounce_type = "soft"

        return recipient, bounce_code, bounce_type, bounce_reason

    def _parse_heuristic(self, msg: Message) -> BounceInfo:
        """Parse bounce using heuristics for non-standard bounces."""
        original_id: str | None = None
        bounce_type: Literal["hard", "soft"] | None = None
        bounce_code: str | None = None
        bounce_reason: str | None = None
        recipient: str | None = None

        # Check subject for bounce indicators
        subject = msg.get("Subject", "")
        is_bounce = any(p.search(subject) for p in self.BOUNCE_SUBJECT_PATTERNS)

        if not is_bounce:
            # Check From header for common bounce senders
            from_addr = msg.get("From", "").lower()
            is_bounce = any(
                pattern in from_addr
                for pattern in ["mailer-daemon", "postmaster", "mail-daemon", "maildelivery"]
            )

        if not is_bounce:
            return BounceInfo(None, None, None, None, None)

        # Search body for SMTP codes and reasons
        body = self._get_text_body(msg)
        if body:
            # Find SMTP code
            match = self.SMTP_CODE_PATTERN.search(body)
            if match:
                bounce_code = match.group(1)
                bounce_type = "hard" if bounce_code.startswith("5") else "soft"

            # Extract first few lines as reason (limit length)
            lines = [l.strip() for l in body.split("\n") if l.strip()][:5]
            bounce_reason = " ".join(lines)[:500]

            # Try to find recipient email
            email_pattern = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")
            emails = email_pattern.findall(body[:1000])
            if emails:
                recipient = emails[0]

        # Try to find original message ID in body or attachments
        original_id = self._find_original_id_in_message(msg)

        return BounceInfo(
            original_message_id=original_id,
            bounce_type=bounce_type or "hard",  # Default to hard if we detected a bounce
            bounce_code=bounce_code,
            bounce_reason=bounce_reason,
            recipient=recipient,
        )

    def _extract_original_id(self, part: Message) -> str | None:
        """Extract X-Genro-Mail-ID from original message part."""
        payload = part.get_payload()
        if isinstance(payload, list) and payload:
            inner_msg = payload[0]
            if hasattr(inner_msg, "get"):
                return inner_msg.get("X-Genro-Mail-ID")
        elif isinstance(payload, str):
            # Search for header in text
            match = re.search(r"X-Genro-Mail-ID:\s*(\S+)", payload, re.I)
            if match:
                return match.group(1).strip()
        return None

    def _find_original_id_in_message(self, msg: Message) -> str | None:
        """Search for X-Genro-Mail-ID anywhere in the message."""
        for part in msg.walk():
            # Check headers
            original_id = part.get("X-Genro-Mail-ID")
            if original_id:
                return original_id

            # Check body text
            if part.get_content_type() in ("text/plain", "text/html", "message/rfc822"):
                payload = part.get_payload(decode=True)
                if isinstance(payload, bytes):
                    text = payload.decode("utf-8", errors="replace")
                    match = re.search(r"X-Genro-Mail-ID:\s*(\S+)", text, re.I)
                    if match:
                        return match.group(1).strip()

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


__all__ = ["BounceInfo", "BounceParser"]
