# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""Helper functions and constants for fullstack integration tests.

Every service address is overridable through a ``GMP_TEST_*`` environment
variable; the defaults reproduce the ports the compose stack publishes.
"""

from __future__ import annotations

import asyncio
import base64
import imaplib
import os
import time
from datetime import datetime, timezone
from email.message import Message
from email.mime.message import MIMEMessage
from email.mime.multipart import MIMEMultipart
from email.mime.nonmultipart import MIMENonMultipart
from email.mime.text import MIMEText
from typing import Any
import uuid

import httpx

from tests import api_routes

# ============================================
# SERVICE URLs AND CONFIGURATION
# ============================================


def _env_port(name: str, default: int) -> int:
    """Host port of a test service, overridable by environment."""
    return int(os.environ.get(name, default))


TEST_HOST = os.environ.get("GMP_TEST_HOST", "localhost")

MAILPROXY_URL = f"http://{TEST_HOST}:{_env_port('GMP_TEST_MAILPROXY_PORT', 8000)}"
MAILPROXY_TOKEN = "test-api-token"

MAILHOG_TENANT1_SMTP = (TEST_HOST, _env_port("GMP_TEST_MAILHOG1_SMTP_PORT", 1025))
MAILHOG_TENANT1_API = f"http://{TEST_HOST}:{_env_port('GMP_TEST_MAILHOG1_API_PORT', 8025)}"
MAILHOG_TENANT2_SMTP = (TEST_HOST, _env_port("GMP_TEST_MAILHOG2_SMTP_PORT", 1026))
MAILHOG_TENANT2_API = f"http://{TEST_HOST}:{_env_port('GMP_TEST_MAILHOG2_API_PORT', 8026)}"

CLIENT_TENANT1_URL = f"http://{TEST_HOST}:{_env_port('GMP_TEST_CLIENT1_PORT', 8081)}"
CLIENT_TENANT2_URL = f"http://{TEST_HOST}:{_env_port('GMP_TEST_CLIENT2_PORT', 8082)}"
ATTACHMENT_SERVER_URL = f"http://{TEST_HOST}:{_env_port('GMP_TEST_ATTACHMENTS_PORT', 8083)}"

MINIO_URL = f"http://{TEST_HOST}:{_env_port('GMP_TEST_MINIO_PORT', 9000)}"

# IMAP server for bounce testing (Dovecot)
DOVECOT_IMAP_HOST = TEST_HOST
DOVECOT_IMAP_PORT = _env_port("GMP_TEST_DOVECOT_IMAP_PORT", 10143)  # Non-SSL IMAP
DOVECOT_BOUNCE_USER = "bounces@localhost"
DOVECOT_BOUNCE_PASS = "bouncepass"
DOVECOT_POLL_INTERVAL = 2  # Fast polling for tests


def is_dovecot_available() -> bool:
    """Check if Dovecot IMAP server is available."""
    try:
        imap = imaplib.IMAP4(DOVECOT_IMAP_HOST, DOVECOT_IMAP_PORT)
        imap.login(DOVECOT_BOUNCE_USER, DOVECOT_BOUNCE_PASS)
        imap.logout()
        return True
    except Exception:
        return False


# Error-simulating SMTP servers (Docker network names and external ports)
SMTP_REJECT_HOST = "smtp-reject"
SMTP_REJECT_PORT = _env_port("GMP_TEST_SMTP_REJECT_PORT", 1027)
SMTP_TEMPFAIL_HOST = "smtp-tempfail"
SMTP_TEMPFAIL_PORT = _env_port("GMP_TEST_SMTP_TEMPFAIL_PORT", 1028)
SMTP_TIMEOUT_HOST = "smtp-timeout"
SMTP_TIMEOUT_PORT = _env_port("GMP_TEST_SMTP_TIMEOUT_PORT", 1029)
SMTP_RATELIMIT_HOST = "smtp-ratelimit"
SMTP_RATELIMIT_PORT = _env_port("GMP_TEST_SMTP_RATELIMIT_PORT", 1030)
SMTP_RANDOM_HOST = "smtp-random"
SMTP_RANDOM_PORT = _env_port("GMP_TEST_SMTP_RANDOM_PORT", 1031)


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
    """Trigger message dispatch for a specific tenant.

    Returns as soon as the proxy accepts the command. Wait for the outcome
    with ``wait_for_message_status``, never with a fixed sleep.
    """
    await api_client.post(api_routes.run_now(tenant_id))


def get_msg_status(msg: dict[str, Any]) -> str:
    """Derive message status from MessageRecord fields.

    The API returns MessageRecord with timestamps, not a status field.
    This helper derives the logical status.

    Priority: error > sent > deferred > pending
    Error takes precedence because a message with both sent_ts and error
    means the send was attempted but failed (e.g., rate limit rejection).
    """
    if msg.get("error_ts") or msg.get("error"):
        return "error"
    if msg.get("sent_ts"):
        return "sent"
    if msg.get("deferred_ts"):
        return "deferred"
    return "pending"


async def wait_for_message_status(
    api_client,
    msg_id: str,
    statuses: tuple[str, ...],
    tenant_id: str = "test-tenant-1",
    timeout: float = 30.0,
) -> dict[str, Any] | None:
    """Wait until a queued message reaches one of the expected statuses.

    Polls GET /messages instead of sleeping for the length of a dispatch
    cycle. Returns the message dict, or None if the timeout expires.
    """
    start = time.time()
    while time.time() - start < timeout:
        resp = await api_client.get(api_routes.messages(tenant_id=tenant_id))
        found = [m for m in resp.json().get("messages", []) if m.get("id") == msg_id]
        if found and get_msg_status(found[0]) in statuses:
            return found[0]
        await asyncio.sleep(0.5)
    return None


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
    # RFC 3464 uses message/delivery-status, but Python's email library
    # doesn't handle it well. Use text/delivery-status which is also valid
    # and our BounceParser accepts both formats.
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
        resp = await api_client.get(api_routes.messages(tenant_id=tenant_id))
        messages = resp.json().get("messages", [])
        found = [m for m in messages if m.get("id") == msg_id]
        if found and found[0].get("bounce_ts"):
            return found[0]
        await asyncio.sleep(2)  # Slightly more than poll interval
    return None


# ============================================
# PEC RECEIPT HELPERS
# ============================================

# PEC IMAP credentials (separate mailbox for PEC receipts)
DOVECOT_PEC_USER = "pec@localhost"
DOVECOT_PEC_PASS = "pecpass"


def create_pec_receipt_email(
    original_message_id: str,
    receipt_type: str = "accettazione",
    recipient: str | None = None,
    error_reason: str | None = None,
) -> bytes:
    """Create a PEC receipt email (ricevuta).

    Args:
        original_message_id: X-Genro-Mail-ID of the original message
        receipt_type: Type of receipt (accettazione, consegna, mancata_consegna, non_accettazione)
        recipient: Email recipient (for consegna receipts)
        error_reason: Error description (for mancata_consegna)

    Returns:
        Raw email bytes
    """
    # Map receipt type to subject prefix and X-Ricevuta header value
    receipt_map = {
        "accettazione": ("ACCETTAZIONE", "accettazione"),
        "consegna": ("AVVENUTA CONSEGNA", "avvenuta-consegna"),
        "mancata_consegna": ("MANCATA CONSEGNA", "mancata-consegna"),
        "non_accettazione": ("NON ACCETTAZIONE", "non-accettazione"),
        "presa_in_carico": ("PRESA IN CARICO", "presa-in-carico"),
    }

    subject_prefix, x_ricevuta = receipt_map.get(receipt_type, ("ACCETTAZIONE", "accettazione"))

    # Build subject
    subject = f"{subject_prefix}: Test message"
    if recipient and receipt_type == "consegna":
        subject = f"POSTA CERTIFICATA: AVVENUTA CONSEGNA per {recipient}"

    # Create the receipt email
    msg = MIMEText(_build_pec_receipt_body(receipt_type, recipient, error_reason), "plain", "utf-8")
    msg["From"] = "posta-certificata@pec.provider.it"
    msg["To"] = "sender@pec.example.com"
    msg["Subject"] = subject
    msg["Date"] = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S %z")
    msg["Message-ID"] = f"<pec-receipt-{uuid.uuid4()}@pec.provider.it>"
    msg["X-Ricevuta"] = x_ricevuta
    msg["X-Genro-Mail-ID"] = original_message_id

    if error_reason and receipt_type in ("mancata_consegna", "non_accettazione"):
        msg["X-Errore"] = error_reason

    return msg.as_bytes()


def _build_pec_receipt_body(
    receipt_type: str,
    recipient: str | None,
    error_reason: str | None,
) -> str:
    """Build the body text for a PEC receipt."""
    if receipt_type == "accettazione":
        return (
            "Ricevuta di accettazione\n\n"
            "Il messaggio e stato accettato dal sistema di posta certificata.\n"
        )
    elif receipt_type == "consegna":
        return (
            f"Ricevuta di avvenuta consegna\n\n"
            f"Il messaggio e stato consegnato al destinatario {recipient or 'dest@pec.it'}.\n"
        )
    elif receipt_type == "mancata_consegna":
        return (
            f"Ricevuta di mancata consegna\n\n"
            f"Errore: {error_reason or 'Destinatario sconosciuto'}\n"
            f"Il messaggio non e stato consegnato.\n"
        )
    elif receipt_type == "non_accettazione":
        return (
            f"Ricevuta di non accettazione\n\n"
            f"Errore: {error_reason or 'Messaggio non valido'}\n"
            f"Il messaggio non e stato accettato.\n"
        )
    else:
        return "Ricevuta PEC.\n"


async def inject_pec_receipt_to_imap(
    receipt_email: bytes,
    host: str = DOVECOT_IMAP_HOST,
    port: int = DOVECOT_IMAP_PORT,
    user: str = DOVECOT_PEC_USER,
    password: str = DOVECOT_PEC_PASS,
) -> bool:
    """Inject a PEC receipt email into the IMAP mailbox.

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
            receipt_email,
        )

        imap.logout()
        return result == "OK"
    except Exception as e:
        print(f"Failed to inject PEC receipt: {e}")
        return False


async def clear_pec_imap_mailbox(
    host: str = DOVECOT_IMAP_HOST,
    port: int = DOVECOT_IMAP_PORT,
    user: str = DOVECOT_PEC_USER,
    password: str = DOVECOT_PEC_PASS,
) -> None:
    """Clear all messages from the PEC IMAP mailbox."""
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


async def get_pec_imap_message_count(
    host: str = DOVECOT_IMAP_HOST,
    port: int = DOVECOT_IMAP_PORT,
    user: str = DOVECOT_PEC_USER,
    password: str = DOVECOT_PEC_PASS,
) -> int:
    """Get the number of messages in the PEC IMAP mailbox."""
    try:
        imap = imaplib.IMAP4(host, port)
        imap.login(user, password)
        result, data = imap.select("INBOX")
        count = int(data[0]) if result == "OK" and data[0] else 0
        imap.logout()
        return count
    except Exception:
        return -1


def is_pec_imap_available() -> bool:
    """Check if PEC IMAP mailbox is available."""
    try:
        imap = imaplib.IMAP4(DOVECOT_IMAP_HOST, DOVECOT_IMAP_PORT)
        imap.login(DOVECOT_PEC_USER, DOVECOT_PEC_PASS)
        imap.logout()
        return True
    except Exception:
        return False


async def wait_for_pec_event(
    api_client,
    msg_id: str,
    event_type: str,
    tenant_id: str = "test-tenant-1",
    timeout: float = 30.0,
) -> dict[str, Any] | None:
    """Wait for a PEC event to be recorded for a message.

    Reads the event history the message list carries with
    ``include_history=true``. Returns the event dict if found, None otherwise.
    """
    start = time.time()
    while time.time() - start < timeout:
        resp = await api_client.get(
            api_routes.messages(tenant_id=tenant_id, include_history=True)
        )
        if resp.status_code == 200:
            for msg in resp.json().get("messages", []):
                if msg.get("id") != msg_id:
                    continue
                for event in msg.get("history") or []:
                    if event.get("event_type") == event_type:
                        return event
        await asyncio.sleep(0.5)
    return None


# ============================================
# PROGRAMMABLE CLIENT CONTROL
# ============================================

class ClientControl:
    """Drives the control surface of one programmable fake client.

    The fake client answers ``/proxy_sync`` and ``/proxy_get_attachments``
    with what a test queued here, in order, and records every call the proxy
    made. See tests/docker/programmable-client/server.py.
    """

    def __init__(self, base_url: str):
        self.base_url = base_url

    async def _post(self, path: str, payload: dict[str, Any]) -> None:
        async with httpx.AsyncClient(base_url=self.base_url, timeout=10.0) as client:
            resp = await client.post(path, json=payload)
            resp.raise_for_status()

    async def queue_sync_response(
        self, body: dict[str, Any], status: int = 200
    ) -> None:
        """Queue one answer for the next /proxy_sync call."""
        await self._post(api_routes.CONTROL_SYNC_RESPONSE, {"status": status, "json": body})

    async def queue_attachment_response(
        self, content: bytes | None = None, status: int = 200
    ) -> None:
        """Queue one answer for the next attachment fetch — bytes, or a bare status."""
        payload: dict[str, Any] = {"status": status}
        if content is not None:
            payload["content_base64"] = base64.b64encode(content).decode()
        await self._post(api_routes.CONTROL_ATTACHMENT_RESPONSE, payload)

    async def get_call_history(self) -> list[dict[str, Any]]:
        """Every call the fake client received since the last reset."""
        async with httpx.AsyncClient(base_url=self.base_url, timeout=10.0) as client:
            resp = await client.get(api_routes.CONTROL_CALLS)
            resp.raise_for_status()
            return resp.json().get("calls", [])

    async def reset(self) -> None:
        """Drop the queued answers and the call history."""
        await self._post(api_routes.CONTROL_RESET, {})
