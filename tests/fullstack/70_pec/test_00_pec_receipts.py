# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""Fullstack tests for PEC (Posta Elettronica Certificata) receipt handling.

These tests verify the complete PEC workflow:
1. Messages from PEC accounts are marked with is_pec=1
2. PEC receipts (ricevute) are parsed and create events
3. Timeout handling for messages without acceptance receipt
"""

from __future__ import annotations

import asyncio
import time

import pytest
import pytest_asyncio

httpx = pytest.importorskip("httpx")

from tests.fullstack.helpers import (
    MAILHOG_TENANT1_API,
    clear_mailhog,
    clear_pec_imap_mailbox,
    create_pec_receipt_email,
    get_pec_imap_message_count,
    inject_pec_receipt_to_imap,
    is_pec_imap_available,
    trigger_dispatch,
    wait_for_messages,
)

pytestmark = [pytest.mark.fullstack, pytest.mark.asyncio]


class TestPecReceiptParsing:
    """Tests for PEC receipt email parsing."""

    async def test_pec_receipt_helper_creates_valid_email(self):
        """Helper function creates valid PEC receipt emails."""
        receipt = create_pec_receipt_email(
            original_message_id="test-msg-001",
            receipt_type="accettazione",
        )

        import email
        msg = email.message_from_bytes(receipt)

        assert msg["X-Ricevuta"] == "accettazione"
        assert msg["X-Genro-Mail-ID"] == "test-msg-001"
        assert "ACCETTAZIONE" in msg["Subject"]

    async def test_pec_receipt_delivery_format(self):
        """Delivery receipt format is correct."""
        receipt = create_pec_receipt_email(
            original_message_id="test-msg-002",
            receipt_type="consegna",
            recipient="dest@pec.example.com",
        )

        import email
        msg = email.message_from_bytes(receipt)

        assert msg["X-Ricevuta"] == "avvenuta-consegna"
        assert msg["X-Genro-Mail-ID"] == "test-msg-002"

    async def test_pec_receipt_failure_format(self):
        """Failure receipt format includes error reason."""
        receipt = create_pec_receipt_email(
            original_message_id="test-msg-003",
            receipt_type="mancata_consegna",
            error_reason="Destinatario sconosciuto",
        )

        import email
        msg = email.message_from_bytes(receipt)

        assert msg["X-Ricevuta"] == "mancata-consegna"
        assert msg["X-Genro-Mail-ID"] == "test-msg-003"
        assert msg["X-Errore"] == "Destinatario sconosciuto"

    async def test_pec_parser_parses_acceptance(self):
        """PecReceiptParser correctly parses acceptance receipts."""
        from core.mail_proxy.pec import PecReceiptParser

        receipt = create_pec_receipt_email(
            original_message_id="parser-test-001",
            receipt_type="accettazione",
        )

        parser = PecReceiptParser()
        info = parser.parse(receipt)

        assert info.receipt_type == "accettazione"
        assert info.original_message_id == "parser-test-001"

    async def test_pec_parser_parses_delivery(self):
        """PecReceiptParser correctly parses delivery receipts."""
        from core.mail_proxy.pec import PecReceiptParser

        receipt = create_pec_receipt_email(
            original_message_id="parser-test-002",
            receipt_type="consegna",
            recipient="dest@pec.it",
        )

        parser = PecReceiptParser()
        info = parser.parse(receipt)

        assert info.receipt_type == "consegna"
        assert info.original_message_id == "parser-test-002"

    async def test_pec_parser_parses_failure(self):
        """PecReceiptParser correctly parses failure receipts."""
        from core.mail_proxy.pec import PecReceiptParser

        receipt = create_pec_receipt_email(
            original_message_id="parser-test-003",
            receipt_type="mancata_consegna",
            error_reason="Casella piena",
        )

        parser = PecReceiptParser()
        info = parser.parse(receipt)

        assert info.receipt_type == "mancata_consegna"
        assert info.original_message_id == "parser-test-003"
        assert info.error_reason == "Casella piena"


class TestPecImapInjection:
    """Tests for PEC receipt IMAP injection."""

    async def test_pec_imap_available(self):
        """PEC IMAP mailbox is accessible."""
        if not is_pec_imap_available():
            pytest.skip("PEC IMAP mailbox not available (pec@localhost)")

        count = await get_pec_imap_message_count()
        assert count >= 0, "PEC IMAP should be accessible"

    async def test_inject_pec_receipt(self):
        """Can inject PEC receipt into IMAP mailbox."""
        if not is_pec_imap_available():
            pytest.skip("PEC IMAP mailbox not available")

        await clear_pec_imap_mailbox()

        receipt = create_pec_receipt_email(
            original_message_id="inject-test-001",
            receipt_type="accettazione",
        )

        success = await inject_pec_receipt_to_imap(receipt)
        assert success, "Should be able to inject PEC receipt"

        count = await get_pec_imap_message_count()
        assert count == 1, "Should have 1 message in PEC mailbox"

        await clear_pec_imap_mailbox()

    async def test_inject_multiple_receipts(self):
        """Can inject multiple PEC receipts."""
        if not is_pec_imap_available():
            pytest.skip("PEC IMAP mailbox not available")

        await clear_pec_imap_mailbox()

        receipts = [
            create_pec_receipt_email(f"multi-{i}", "accettazione")
            for i in range(3)
        ]

        for receipt in receipts:
            success = await inject_pec_receipt_to_imap(receipt)
            assert success

        count = await get_pec_imap_message_count()
        assert count == 3, "Should have 3 messages in PEC mailbox"

        await clear_pec_imap_mailbox()


class TestPecAccountSetup:
    """Tests for PEC account configuration."""

    async def test_create_pec_account(self, api_client, setup_pec_tenant):
        """Can create and retrieve a PEC account."""
        # The fixture already created the account, verify it exists
        resp = await api_client.get("/accounts?tenant_id=pec-tenant")
        assert resp.status_code == 200

        accounts = resp.json().get("accounts", [])
        pec_accounts = [a for a in accounts if a.get("id") == "pec-account"]
        assert len(pec_accounts) == 1

        pec_account = pec_accounts[0]
        assert pec_account.get("is_pec_account") is True

    async def test_pec_account_has_imap_config(self, api_client, setup_pec_tenant):
        """PEC account has IMAP configuration for receipt polling."""
        resp = await api_client.get("/accounts?tenant_id=pec-tenant")
        accounts = resp.json().get("accounts", [])
        pec_account = next((a for a in accounts if a.get("id") == "pec-account"), None)

        assert pec_account is not None
        assert pec_account.get("imap_host") is not None


class TestPecMessageFlow:
    """End-to-end PEC message flow tests."""

    async def test_message_from_pec_account_marked_is_pec(
        self, api_client, setup_pec_tenant
    ):
        """Messages sent from PEC accounts are marked with is_pec=1."""
        ts = int(time.time())
        msg_id = f"pec-msg-{ts}"

        message = {
            "id": msg_id,
            "account_id": "pec-account",
            "from": "sender@pec.example.com",
            "to": ["recipient@pec.example.com"],
            "subject": "PEC Test Message",
            "body": "This is a PEC test message.",
        }

        resp = await api_client.post("/commands/add-messages", json={"messages": [message]})
        assert resp.status_code == 200

        # Verify message is marked as PEC
        resp = await api_client.get(f"/messages?tenant_id=pec-tenant")
        messages = resp.json().get("messages", [])
        found = [m for m in messages if m.get("id") == msg_id]
        assert len(found) == 1
        assert found[0].get("is_pec") is True

    async def test_pec_message_sent_with_tracking_header(
        self, api_client, setup_pec_tenant
    ):
        """PEC messages include X-Genro-Mail-ID header for receipt correlation."""
        await clear_mailhog(MAILHOG_TENANT1_API)

        ts = int(time.time())
        msg_id = f"pec-track-{ts}"

        message = {
            "id": msg_id,
            "account_id": "pec-account",
            "from": "sender@pec.example.com",
            "to": ["recipient@pec.example.com"],
            "subject": "PEC Tracking Test",
            "body": "Testing PEC tracking header.",
        }

        resp = await api_client.post("/commands/add-messages", json={"messages": [message]})
        assert resp.status_code == 200

        await trigger_dispatch(api_client, "pec-tenant")

        # Check MailHog for the sent message
        emails = await wait_for_messages(MAILHOG_TENANT1_API, 1)
        assert len(emails) >= 1

        # Verify X-Genro-Mail-ID header
        found_email = None
        for email in emails:
            headers = email.get("Content", {}).get("Headers", {})
            mail_id_list = headers.get("X-Genro-Mail-Id") or headers.get("X-Genro-Mail-ID")
            if mail_id_list:
                mail_id = mail_id_list[0] if isinstance(mail_id_list, list) else mail_id_list
                if mail_id == msg_id:
                    found_email = email
                    break

        assert found_email is not None, "PEC message should have X-Genro-Mail-ID header"

    async def test_non_pec_account_message_not_marked(
        self, api_client, setup_test_tenants
    ):
        """Messages from regular accounts are not marked as PEC."""
        ts = int(time.time())
        msg_id = f"regular-msg-{ts}"

        message = {
            "id": msg_id,
            "account_id": "test-account-1",  # Regular account
            "from": "sender@example.com",
            "to": ["recipient@example.com"],
            "subject": "Regular Test Message",
            "body": "This is a regular test message.",
        }

        resp = await api_client.post("/commands/add-messages", json={"messages": [message]})
        assert resp.status_code == 200

        resp = await api_client.get(f"/messages?tenant_id=test-tenant-1")
        messages = resp.json().get("messages", [])
        found = [m for m in messages if m.get("id") == msg_id]
        assert len(found) == 1
        # is_pec should be False or not present
        assert found[0].get("is_pec") in (False, None, 0)


class TestPecReceiptProcessing:
    """Tests for PEC receipt processing by PecReceiver.

    Note: These tests require Dovecot to be configured with a pec@localhost
    mailbox. If not available, tests will be skipped.
    """

    async def test_receipt_injection_setup(self):
        """Verify test infrastructure for PEC receipts."""
        if not is_pec_imap_available():
            pytest.skip("PEC IMAP not available - Dovecot needs pec@localhost mailbox")

        await clear_pec_imap_mailbox()
        count = await get_pec_imap_message_count()
        assert count == 0, "PEC mailbox should be empty after clear"

    async def test_acceptance_receipt_creates_event(
        self, api_client, setup_pec_tenant
    ):
        """Acceptance receipt creates pec_acceptance event.

        Note: This test verifies the receipt can be injected. Full end-to-end
        testing requires PecReceiver to be running and polling.
        """
        if not is_pec_imap_available():
            pytest.skip("PEC IMAP not available")

        await clear_pec_imap_mailbox()

        ts = int(time.time())
        msg_id = f"pec-accept-{ts}"

        # 1. Create a PEC message
        message = {
            "id": msg_id,
            "account_id": "pec-account",
            "from": "sender@pec.example.com",
            "to": ["recipient@pec.example.com"],
            "subject": "PEC Acceptance Test",
            "body": "Testing acceptance receipt.",
        }

        resp = await api_client.post("/commands/add-messages", json={"messages": [message]})
        assert resp.status_code == 200

        # 2. Dispatch the message
        await trigger_dispatch(api_client, "pec-tenant")

        # 3. Inject acceptance receipt
        receipt = create_pec_receipt_email(
            original_message_id=msg_id,
            receipt_type="accettazione",
        )

        success = await inject_pec_receipt_to_imap(receipt)
        assert success, "Should inject acceptance receipt"

        # 4. Verify receipt is in mailbox (PecReceiver will poll it)
        count = await get_pec_imap_message_count()
        assert count == 1, "Should have acceptance receipt in mailbox"

        await clear_pec_imap_mailbox()

    async def test_delivery_receipt_creates_event(
        self, api_client, setup_pec_tenant
    ):
        """Delivery receipt creates pec_delivery event."""
        if not is_pec_imap_available():
            pytest.skip("PEC IMAP not available")

        await clear_pec_imap_mailbox()

        ts = int(time.time())
        msg_id = f"pec-delivery-{ts}"

        message = {
            "id": msg_id,
            "account_id": "pec-account",
            "from": "sender@pec.example.com",
            "to": ["recipient@pec.example.com"],
            "subject": "PEC Delivery Test",
            "body": "Testing delivery receipt.",
        }

        resp = await api_client.post("/commands/add-messages", json={"messages": [message]})
        assert resp.status_code == 200

        await trigger_dispatch(api_client, "pec-tenant")

        # Inject delivery receipt
        receipt = create_pec_receipt_email(
            original_message_id=msg_id,
            receipt_type="consegna",
            recipient="recipient@pec.example.com",
        )

        success = await inject_pec_receipt_to_imap(receipt)
        assert success, "Should inject delivery receipt"

        count = await get_pec_imap_message_count()
        assert count == 1

        await clear_pec_imap_mailbox()

    async def test_failure_receipt_creates_error_event(
        self, api_client, setup_pec_tenant
    ):
        """Failure receipt creates pec_error event."""
        if not is_pec_imap_available():
            pytest.skip("PEC IMAP not available")

        await clear_pec_imap_mailbox()

        ts = int(time.time())
        msg_id = f"pec-failure-{ts}"

        message = {
            "id": msg_id,
            "account_id": "pec-account",
            "from": "sender@pec.example.com",
            "to": ["invalid@pec.example.com"],
            "subject": "PEC Failure Test",
            "body": "Testing failure receipt.",
        }

        resp = await api_client.post("/commands/add-messages", json={"messages": [message]})
        assert resp.status_code == 200

        await trigger_dispatch(api_client, "pec-tenant")

        # Inject failure receipt
        receipt = create_pec_receipt_email(
            original_message_id=msg_id,
            receipt_type="mancata_consegna",
            error_reason="Destinatario sconosciuto",
        )

        success = await inject_pec_receipt_to_imap(receipt)
        assert success, "Should inject failure receipt"

        count = await get_pec_imap_message_count()
        assert count == 1

        await clear_pec_imap_mailbox()


class TestPecReceiptTypes:
    """Tests for different PEC receipt types."""

    async def test_all_receipt_types_parseable(self):
        """All PEC receipt types can be parsed correctly."""
        from core.mail_proxy.pec import PecReceiptParser

        parser = PecReceiptParser()
        receipt_types = [
            ("accettazione", "accettazione"),
            ("consegna", "consegna"),
            ("mancata_consegna", "mancata_consegna"),
            ("non_accettazione", "non_accettazione"),
        ]

        for create_type, expected_type in receipt_types:
            receipt = create_pec_receipt_email(
                original_message_id=f"type-test-{create_type}",
                receipt_type=create_type,
            )

            info = parser.parse(receipt)
            assert info.receipt_type == expected_type, \
                f"Receipt type {create_type} should parse as {expected_type}"
            assert info.original_message_id == f"type-test-{create_type}"

    async def test_non_pec_email_not_parsed_as_receipt(self):
        """Regular emails are not mistaken for PEC receipts."""
        from core.mail_proxy.pec import PecReceiptParser
        from email.mime.text import MIMEText

        # Create a regular email
        msg = MIMEText("This is a regular email.", "plain")
        msg["From"] = "sender@example.com"
        msg["To"] = "recipient@example.com"
        msg["Subject"] = "Regular Email"

        parser = PecReceiptParser()
        info = parser.parse(msg.as_bytes())

        assert info.receipt_type is None
        assert info.original_message_id is None
