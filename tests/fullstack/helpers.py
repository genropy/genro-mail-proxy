# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""Helper functions and constants for fullstack integration tests."""

from __future__ import annotations

import asyncio
import imaplib
import time
from datetime import datetime, timezone
from email.message import Message
from email.mime.message import MIMEMessage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any
import uuid

import httpx

# ============================================
# SERVICE URLs AND CONFIGURATION
# ============================================

MAILPROXY_URL = "http://localhost:8000"
MAILPROXY_TOKEN = "test-api-token"

MAILHOG_TENANT1_SMTP = ("localhost", 1025)
MAILHOG_TENANT1_API = "http://localhost:8025"
MAILHOG_TENANT2_SMTP = ("localhost", 1026)
MAILHOG_TENANT2_API = "http://localhost:8026"

CLIENT_TENANT1_URL = "http://localhost:8081"
CLIENT_TENANT2_URL = "http://localhost:8082"
ATTACHMENT_SERVER_URL = "http://localhost:8083"

MINIO_URL = "http://localhost:9000"

# IMAP server for bounce testing (Dovecot)
DOVECOT_IMAP_HOST = "localhost"
DOVECOT_IMAP_PORT = 10143  # Non-SSL IMAP
DOVECOT_BOUNCE_USER = "bounces@localhost"
DOVECOT_BOUNCE_PASS = "bouncepass"

# Error-simulating SMTP servers (Docker network names and external ports)
SMTP_REJECT_HOST = "smtp-reject"
SMTP_REJECT_PORT = 1027
SMTP_TEMPFAIL_HOST = "smtp-tempfail"
SMTP_TEMPFAIL_PORT = 1028
SMTP_TIMEOUT_HOST = "smtp-timeout"
SMTP_TIMEOUT_PORT = 1029
SMTP_RATELIMIT_HOST = "smtp-ratelimit"
SMTP_RATELIMIT_PORT = 1030
SMTP_RANDOM_HOST = "smtp-random"
SMTP_RANDOM_PORT = 1031


# ============================================
# MAILHOG HELPERS
# ============================================

async def clear_mailhog(api_url: str) -> None:
    """Clear all messages from a MailHog instance."""
    async with httpx.AsyncClient() as client:
        await client.delete(f"{api_url}/api/v1/messages")


async def get_mailhog_messages(api_url: str) -> list[dict[str, Any]]:
    """Get all messages from a MailHog instance."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{api_url}/api/v2/messages")
        if resp.status_code == 200:
            return resp.json().get("items", [])
        return []


async def wait_for_messages(
    api_url: str, expected_count: int, timeout: float = 10.0
) -> list[dict[str, Any]]:
    """Wait for expected number of messages in MailHog."""
    start = time.time()
    while time.time() - start < timeout:
        messages = await get_mailhog_messages(api_url)
        if len(messages) >= expected_count:
            return messages
        await asyncio.sleep(0.5)
    return await get_mailhog_messages(api_url)


# ============================================
# DISPATCH HELPERS
# ============================================

async def trigger_dispatch(api_client, tenant_id: str = "test-tenant-1") -> None:
    """Trigger message dispatch for a specific tenant."""
    await api_client.post("/commands/run-now", params={"tenant_id": tenant_id})
    await asyncio.sleep(2)  # Wait for processing


def get_msg_status(msg: dict[str, Any]) -> str:
    """Derive message status from MessageRecord fields.

    The API returns MessageRecord with timestamps, not a status field.
    This helper derives the logical status.
    """
    if msg.get("sent_ts"):
        return "sent"
    if msg.get("error_ts") or msg.get("error"):
        return "error"
    if msg.get("deferred_ts"):
        return "deferred"
    return "pending"


# ============================================
# IMAP / BOUNCE HELPERS
# ============================================

def create_dsn_bounce_email(
    original_message_id: str,
    recipient: str = "failed@example.com",
    bounce_code: str = "550",
    bounce_reason: str = "User unknown",
) -> bytes:
    """Create a DSN (RFC 3464) formatted bounce email.

    This creates a properly formatted Delivery Status Notification
    that includes our X-Genro-Mail-ID header for correlation.
    """
    # Create the main multipart/report message
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
    # RFC 3464 format
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

    # Part 3: Original message headers (includes X-Genro-Mail-ID)
    original_headers = Message()
    original_headers["X-Genro-Mail-ID"] = original_message_id
    original_headers["From"] = "sender@test.com"
    original_headers["To"] = recipient
    original_headers["Subject"] = "Original Test Message"
    original_headers["Message-ID"] = f"<original-{original_message_id}@test.com>"

    original_part = MIMEMessage(original_headers, "rfc822-headers")
    msg.attach(original_part)

    return msg.as_bytes()


async def inject_bounce_email_to_imap(
    bounce_email: bytes,
    host: str = DOVECOT_IMAP_HOST,
    port: int = DOVECOT_IMAP_PORT,
    user: str = DOVECOT_BOUNCE_USER,
    password: str = DOVECOT_BOUNCE_PASS,
) -> bool:
    """Inject a bounce email directly into the IMAP mailbox.

    Uses IMAP APPEND command to add the email to the INBOX.
    Returns True if successful.
    """
    try:
        imap = imaplib.IMAP4(host, port)
        imap.login(user, password)
        imap.select("INBOX")

        date_time = imaplib.Time2Internaldate(time.time())
        result, _ = imap.append(
            "INBOX",
            "",  # No flags
            date_time,
            bounce_email,
        )

        imap.logout()
        return result == "OK"
    except Exception as e:
        print(f"Failed to inject bounce email: {e}")
        return False


async def clear_imap_mailbox(
    host: str = DOVECOT_IMAP_HOST,
    port: int = DOVECOT_IMAP_PORT,
    user: str = DOVECOT_BOUNCE_USER,
    password: str = DOVECOT_BOUNCE_PASS,
) -> None:
    """Clear all messages from an IMAP mailbox."""
    try:
        imap = imaplib.IMAP4(host, port)
        imap.login(user, password)
        imap.select("INBOX")

        # Search for all messages
        _, message_ids = imap.search(None, "ALL")
        if message_ids[0]:
            for msg_id in message_ids[0].split():
                imap.store(msg_id, "+FLAGS", "\\Deleted")
            imap.expunge()

        imap.logout()
    except Exception:
        pass  # Ignore errors during cleanup


async def get_imap_message_count(
    host: str = DOVECOT_IMAP_HOST,
    port: int = DOVECOT_IMAP_PORT,
    user: str = DOVECOT_BOUNCE_USER,
    password: str = DOVECOT_BOUNCE_PASS,
) -> int:
    """Get the number of messages in an IMAP mailbox."""
    try:
        imap = imaplib.IMAP4(host, port)
        imap.login(user, password)
        result, data = imap.select("INBOX")
        count = int(data[0]) if result == "OK" and data[0] else 0
        imap.logout()
        return count
    except Exception:
        return -1


async def wait_for_bounce(
    api_client,
    msg_id: str,
    tenant_id: str = "test-tenant-1",
    timeout: float = 30.0,
) -> dict[str, Any] | None:
    """Wait for BounceReceiver to detect and process a bounce.

    Polls the API until bounce_ts is populated or timeout.
    Returns the message dict if bounce detected, None otherwise.
    """
    start = time.time()
    while time.time() - start < timeout:
        resp = await api_client.get(f"/messages?tenant_id={tenant_id}")
        messages = resp.json().get("messages", [])
        found = [m for m in messages if m.get("id") == msg_id]
        if found and found[0].get("bounce_ts"):
            return found[0]
        await asyncio.sleep(2)  # Slightly more than poll interval
    return None
