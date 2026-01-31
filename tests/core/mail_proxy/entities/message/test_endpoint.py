# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Tests for MessageEndpoint - direct endpoint tests for coverage.

These tests directly exercise MessageEndpoint methods to cover
edge cases and error paths not reached by HTTP client tests.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from core.mail_proxy.entities.message.endpoint import (
    MessageEndpoint,
    MessageStatus,
    FetchMode,
    AttachmentPayload,
)


@pytest.fixture
def mock_table():
    """Create mock MessagesTable."""
    table = MagicMock()
    table.insert_batch = AsyncMock(return_value=[{"id": "test", "pk": "pk-123"}])
    table.get = AsyncMock(return_value=None)
    table.list_all = AsyncMock(return_value=[])
    table.remove_by_pk = AsyncMock(return_value=True)
    table.count_active = AsyncMock(return_value=0)
    table.count_pending_for_tenant = AsyncMock(return_value=0)
    table.get_ids_for_tenant = AsyncMock(return_value=set())
    table.existing_ids = AsyncMock(return_value=set())
    table.remove_fully_reported_before_for_tenant = AsyncMock(return_value=0)
    return table


@pytest.fixture
def endpoint(mock_table):
    """Create MessageEndpoint with mock table."""
    return MessageEndpoint(mock_table)


class TestFetchModeEnum:
    """Tests for FetchMode enum values."""

    def test_fetch_mode_endpoint(self):
        """FetchMode.ENDPOINT has correct value."""
        assert FetchMode.ENDPOINT.value == "endpoint"

    def test_fetch_mode_http_url(self):
        """FetchMode.HTTP_URL has correct value."""
        assert FetchMode.HTTP_URL.value == "http_url"

    def test_fetch_mode_base64(self):
        """FetchMode.BASE64 has correct value."""
        assert FetchMode.BASE64.value == "base64"

    def test_fetch_mode_filesystem(self):
        """FetchMode.FILESYSTEM has correct value."""
        assert FetchMode.FILESYSTEM.value == "filesystem"


class TestMessageStatusEnum:
    """Tests for MessageStatus enum values."""

    def test_status_pending(self):
        """MessageStatus.PENDING has correct value."""
        assert MessageStatus.PENDING.value == "pending"

    def test_status_deferred(self):
        """MessageStatus.DEFERRED has correct value."""
        assert MessageStatus.DEFERRED.value == "deferred"

    def test_status_sent(self):
        """MessageStatus.SENT has correct value."""
        assert MessageStatus.SENT.value == "sent"

    def test_status_error(self):
        """MessageStatus.ERROR has correct value."""
        assert MessageStatus.ERROR.value == "error"


class TestAttachmentPayload:
    """Tests for AttachmentPayload model."""

    def test_attachment_minimal(self):
        """AttachmentPayload with required fields only."""
        payload = AttachmentPayload(
            filename="report.pdf",
            storage_path="/data/report.pdf",
        )
        assert payload.filename == "report.pdf"
        assert payload.storage_path == "/data/report.pdf"
        assert payload.mime_type is None
        assert payload.fetch_mode is None
        assert payload.content_md5 is None
        assert payload.auth is None

    def test_attachment_full(self):
        """AttachmentPayload with all fields."""
        payload = AttachmentPayload(
            filename="doc.pdf",
            storage_path="https://cdn.example.com/doc.pdf",
            mime_type="application/pdf",
            fetch_mode=FetchMode.HTTP_URL,
            content_md5="d41d8cd98f00b204e9800998ecf8427e",
            auth={"bearer": "token123"},
        )
        assert payload.fetch_mode == FetchMode.HTTP_URL
        assert payload.content_md5 == "d41d8cd98f00b204e9800998ecf8427e"
        assert payload.auth == {"bearer": "token123"}

    def test_attachment_invalid_md5_rejected(self):
        """AttachmentPayload rejects invalid MD5 hash."""
        with pytest.raises(ValueError):
            AttachmentPayload(
                filename="test.pdf",
                storage_path="/data/test.pdf",
                content_md5="invalid-not-32-hex-chars",
            )


class TestMessageEndpointAdd:
    """Tests for MessageEndpoint.add() method."""

    async def test_add_with_return_path(self, endpoint, mock_table):
        """add() includes return_path in payload."""
        result = await endpoint.add(
            id="msg-001",
            tenant_id="t1",
            account_id="a1",
            from_addr="sender@test.com",
            to=["recipient@test.com"],
            subject="Test",
            body="Body",
            return_path="bounce@test.com",
        )
        assert result["id"] == "test"
        # Verify return_path was included in payload
        call_args = mock_table.insert_batch.call_args[0][0][0]
        assert call_args["payload"]["return_path"] == "bounce@test.com"

    async def test_add_with_message_id(self, endpoint, mock_table):
        """add() includes custom message_id in payload."""
        result = await endpoint.add(
            id="msg-002",
            tenant_id="t1",
            account_id="a1",
            from_addr="sender@test.com",
            to=["recipient@test.com"],
            subject="Test",
            body="Body",
            message_id="<custom-123@test.com>",
        )
        assert result["id"] == "test"
        call_args = mock_table.insert_batch.call_args[0][0][0]
        assert call_args["payload"]["message_id"] == "<custom-123@test.com>"

    async def test_add_with_attachments(self, endpoint, mock_table):
        """add() includes attachments in payload."""
        attachments = [
            {"filename": "doc.pdf", "storage_path": "/data/doc.pdf"},
        ]
        result = await endpoint.add(
            id="msg-003",
            tenant_id="t1",
            account_id="a1",
            from_addr="sender@test.com",
            to=["recipient@test.com"],
            subject="Test",
            body="Body",
            attachments=attachments,
        )
        assert result["id"] == "test"
        call_args = mock_table.insert_batch.call_args[0][0][0]
        assert call_args["payload"]["attachments"] == attachments

    async def test_add_fails_raises_value_error(self, endpoint, mock_table):
        """add() raises ValueError when insert_batch returns empty."""
        mock_table.insert_batch = AsyncMock(return_value=[])
        with pytest.raises(ValueError, match="Failed to add message"):
            await endpoint.add(
                id="msg-fail",
                tenant_id="t1",
                account_id="a1",
                from_addr="sender@test.com",
                to=["recipient@test.com"],
                subject="Test",
                body="Body",
            )


class TestMessageEndpointGet:
    """Tests for MessageEndpoint.get() method."""

    async def test_get_not_found_raises(self, endpoint, mock_table):
        """get() raises ValueError when message not found."""
        mock_table.get = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="Message 'nonexistent' not found"):
            await endpoint.get("nonexistent", "t1")


class TestMessageEndpointAddBatch:
    """Tests for add_batch() validation paths."""

    async def test_add_batch_missing_id(self, endpoint, mock_table):
        """add_batch() rejects message without id."""
        result = await endpoint.add_batch([
            {"tenant_id": "t1", "account_id": "a1", "from": "a@b.com", "to": ["c@d.com"], "subject": "Hi"},
        ])
        assert result["queued"] == 0
        assert len(result["rejected"]) == 1
        assert result["rejected"][0]["id"] is None
        assert "Missing 'id'" in result["rejected"][0]["reason"]

    async def test_add_batch_missing_tenant_id(self, endpoint, mock_table):
        """add_batch() rejects message without tenant_id."""
        result = await endpoint.add_batch([
            {"id": "m1", "account_id": "a1", "from": "a@b.com", "to": ["c@d.com"], "subject": "Hi"},
        ])
        assert result["queued"] == 0
        assert result["rejected"][0]["id"] == "m1"
        assert "Missing 'tenant_id'" in result["rejected"][0]["reason"]

    async def test_add_batch_missing_account_id(self, endpoint, mock_table):
        """add_batch() rejects message without account_id."""
        result = await endpoint.add_batch([
            {"id": "m1", "tenant_id": "t1", "from": "a@b.com", "to": ["c@d.com"], "subject": "Hi"},
        ])
        assert result["queued"] == 0
        assert result["rejected"][0]["id"] == "m1"
        assert "Missing 'account_id'" in result["rejected"][0]["reason"]

    async def test_add_batch_missing_from(self, endpoint, mock_table):
        """add_batch() rejects message without from field."""
        result = await endpoint.add_batch([
            {"id": "m1", "tenant_id": "t1", "account_id": "a1", "to": ["c@d.com"], "subject": "Hi"},
        ])
        assert result["queued"] == 0
        assert "Missing 'from'" in result["rejected"][0]["reason"]

    async def test_add_batch_missing_to(self, endpoint, mock_table):
        """add_batch() rejects message without to field."""
        result = await endpoint.add_batch([
            {"id": "m1", "tenant_id": "t1", "account_id": "a1", "from": "a@b.com", "subject": "Hi"},
        ])
        assert result["queued"] == 0
        assert "Missing 'to'" in result["rejected"][0]["reason"]

    async def test_add_batch_missing_subject(self, endpoint, mock_table):
        """add_batch() rejects message without subject field."""
        result = await endpoint.add_batch([
            {"id": "m1", "tenant_id": "t1", "account_id": "a1", "from": "a@b.com", "to": ["c@d.com"]},
        ])
        assert result["queued"] == 0
        assert "Missing 'subject'" in result["rejected"][0]["reason"]

    async def test_add_batch_accepts_from_addr(self, endpoint, mock_table):
        """add_batch() accepts from_addr as alternative to from."""
        mock_table.insert_batch = AsyncMock(return_value=[{"id": "m1", "pk": "pk-1"}])
        result = await endpoint.add_batch([
            {"id": "m1", "tenant_id": "t1", "account_id": "a1", "from_addr": "a@b.com", "to": ["c@d.com"], "subject": "Hi"},
        ])
        assert result["queued"] == 1
        assert len(result["rejected"]) == 0


class TestMessageEndpointDeleteBatch:
    """Tests for delete_batch() authorization paths."""

    async def test_delete_batch_unauthorized(self, endpoint, mock_table):
        """delete_batch() reports unauthorized messages."""
        # Setup: msg exists but belongs to different tenant
        mock_table.get_ids_for_tenant = AsyncMock(return_value=set())  # Not in tenant
        mock_table.existing_ids = AsyncMock(return_value={"msg-other"})  # But exists

        result = await endpoint.delete_batch("t1", ["msg-other"])

        assert result["ok"] is True
        assert result["removed"] == 0
        assert "msg-other" in result["unauthorized"]

    async def test_delete_batch_not_found(self, endpoint, mock_table):
        """delete_batch() reports not found messages."""
        mock_table.get_ids_for_tenant = AsyncMock(return_value=set())
        mock_table.existing_ids = AsyncMock(return_value=set())  # Doesn't exist

        result = await endpoint.delete_batch("t1", ["nonexistent"])

        assert result["ok"] is True
        assert result["removed"] == 0
        assert "nonexistent" in result["not_found"]

    async def test_delete_batch_remove_fails(self, endpoint, mock_table):
        """delete_batch() handles remove failure."""
        mock_table.get_ids_for_tenant = AsyncMock(return_value={"msg-1"})
        mock_table.get = AsyncMock(return_value={"pk": "pk-1"})
        mock_table.remove_by_pk = AsyncMock(return_value=False)  # Fails

        result = await endpoint.delete_batch("t1", ["msg-1"])

        assert result["ok"] is True
        assert result["removed"] == 0
        assert "msg-1" in result["not_found"]


class TestMessageEndpointAddStatus:
    """Tests for _add_status() helper method."""

    def test_add_status_sent_success(self, endpoint):
        """_add_status() marks sent when smtp_ts and no error."""
        msg = {"smtp_ts": 12345, "error": None}
        result = endpoint._add_status(msg)
        assert result["status"] == "sent"

    def test_add_status_sent_with_error(self, endpoint):
        """_add_status() marks error when smtp_ts and error set."""
        msg = {"smtp_ts": 12345, "error": "SMTP connection failed"}
        result = endpoint._add_status(msg)
        assert result["status"] == "error"

    def test_add_status_deferred(self, endpoint):
        """_add_status() marks deferred when deferred_ts set."""
        msg = {"smtp_ts": None, "deferred_ts": 99999, "error": None}
        result = endpoint._add_status(msg)
        assert result["status"] == "deferred"

    def test_add_status_pending(self, endpoint):
        """_add_status() marks pending when no smtp_ts or deferred_ts."""
        msg = {"smtp_ts": None, "deferred_ts": None, "error": None}
        result = endpoint._add_status(msg)
        assert result["status"] == "pending"
