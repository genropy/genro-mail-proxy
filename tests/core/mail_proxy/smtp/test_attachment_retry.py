# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Tests for transient attachment fetch failure handling in SmtpSender."""

from email.message import EmailMessage
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from core.mail_proxy.smtp.sender import (
    AttachmentFetchError,
    SmtpSender,
    _is_transient_fetch_error,
)

from .test_sender_extended import MockProxy


def _response_error(status):
    return aiohttp.ClientResponseError(
        request_info=MagicMock(), history=(), status=status, message="err"
    )


class TestFetchErrorClassification:
    """Tests for _is_transient_fetch_error."""

    @pytest.mark.parametrize("status", [400, 404, 410])
    def test_permanent_statuses(self, status):
        assert _is_transient_fetch_error(_response_error(status)) is False

    @pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
    def test_transient_statuses(self, status):
        assert _is_transient_fetch_error(_response_error(status)) is True

    def test_timeout_is_transient(self):
        assert _is_transient_fetch_error(TimeoutError("fetch timed out")) is True

    def test_connection_error_is_transient(self):
        assert _is_transient_fetch_error(aiohttp.ClientConnectorError(
            MagicMock(), OSError("connection refused"))) is True

    def test_unknown_error_is_transient(self):
        assert _is_transient_fetch_error(RuntimeError("boom")) is True


class TestProcessAttachmentsErrors:
    """Tests for AttachmentFetchError raised by _process_attachments."""

    @pytest.fixture
    def mock_proxy(self):
        return MockProxy()

    @pytest.fixture
    def sender(self, mock_proxy):
        s = SmtpSender(mock_proxy)
        s._get_large_file_config_for_message = AsyncMock(return_value=None)
        s._get_attachment_manager_for_message = AsyncMock(return_value=MagicMock())
        return s

    async def test_transient_fetch_failure_raises_transient_error(self, sender):
        sender._fetch_attachment_with_timeout = AsyncMock(side_effect=_response_error(503))

        with pytest.raises(AttachmentFetchError) as excinfo:
            await sender._process_attachments(
                EmailMessage(), {}, [{"filename": "f.pdf", "storage_path": "x"}], "plain"
            )
        assert excinfo.value.transient is True
        assert "f.pdf" in str(excinfo.value)

    async def test_missing_file_raises_permanent_error(self, sender):
        sender._fetch_attachment_with_timeout = AsyncMock(side_effect=_response_error(404))

        with pytest.raises(AttachmentFetchError) as excinfo:
            await sender._process_attachments(
                EmailMessage(), {}, [{"filename": "f.pdf", "storage_path": "x"}], "plain"
            )
        assert excinfo.value.transient is False

    async def test_no_data_raises_permanent_error(self, sender):
        sender._fetch_attachment_with_timeout = AsyncMock(return_value=None)

        with pytest.raises(AttachmentFetchError) as excinfo:
            await sender._process_attachments(
                EmailMessage(), {}, [{"filename": "f.pdf", "storage_path": "x"}], "plain"
            )
        assert excinfo.value.transient is False


class TestDispatchAttachmentFailure:
    """Tests for _dispatch_message handling of AttachmentFetchError."""

    @pytest.fixture
    def mock_proxy(self):
        proxy = MockProxy()
        proxy._retry_strategy.max_retries = 5
        proxy._retry_strategy.calculate_delay = MagicMock(return_value=60)
        proxy._tables["messages"].clear_deferred = AsyncMock()
        proxy._tables["messages"].update_payload = AsyncMock()
        proxy._tables["message_events"].add_event = AsyncMock()
        return proxy

    @pytest.fixture
    def sender(self, mock_proxy):
        s = SmtpSender(mock_proxy)
        s._send_with_limits = AsyncMock()
        s._publish_result = AsyncMock()
        return s

    def _entry(self, retry_count=None):
        message = {
            "from": "sender@test.com",
            "to": ["recipient@test.com"],
            "subject": "Test",
            "body": "Hello",
        }
        if retry_count is not None:
            message["retry_count"] = retry_count
        return {
            "pk": "pk-1",
            "id": "msg-1",
            "tenant_id": "t1",
            "account_id": "a1",
            "message": message,
        }

    async def test_transient_failure_defers_message(self, sender, mock_proxy):
        sender._build_email = AsyncMock(
            side_effect=AttachmentFetchError("f.pdf", "503 endpoint down", transient=True)
        )

        await sender._dispatch_message(self._entry(), 12345)

        updated = mock_proxy._tables["messages"].update_payload.call_args[0][1]
        assert updated["retry_count"] == 1
        assert updated["from"] == "sender@test.com"
        assert "message" not in updated  # payload, not the whole entry

        event_call = mock_proxy._tables["message_events"].add_event.call_args
        assert event_call[0][1] == "deferred"
        assert event_call[1]["metadata"]["deferred_ts"] == 12345 + 60

        published = sender._publish_result.call_args[0][0]
        assert published["status"] == "deferred"
        assert published["retry_count"] == 1
        sender._send_with_limits.assert_not_called()

    async def test_transient_failure_over_max_retries_errors(self, sender, mock_proxy):
        sender._build_email = AsyncMock(
            side_effect=AttachmentFetchError("f.pdf", "503 endpoint down", transient=True)
        )

        await sender._dispatch_message(self._entry(retry_count=5), 12345)

        mock_proxy._tables["messages"].update_payload.assert_not_called()
        event_call = mock_proxy._tables["message_events"].add_event.call_args
        assert event_call[0][1] == "error"
        assert "Max retries (5) exceeded" in event_call[1]["description"]

        published = sender._publish_result.call_args[0][0]
        assert published["status"] == "error"

    async def test_permanent_failure_errors_immediately(self, sender, mock_proxy):
        sender._build_email = AsyncMock(
            side_effect=AttachmentFetchError("f.pdf", "404 not found", transient=False)
        )

        await sender._dispatch_message(self._entry(), 12345)

        mock_proxy._tables["messages"].update_payload.assert_not_called()
        event_call = mock_proxy._tables["message_events"].add_event.call_args
        assert event_call[0][1] == "error"
        assert "Max retries" not in event_call[1]["description"]

        published = sender._publish_result.call_args[0][0]
        assert published["status"] == "error"


class TestRetryCountPersistence:
    """retry_count must be stored inside the message payload, not the entry."""

    @pytest.fixture
    def mock_proxy(self):
        proxy = MockProxy()
        proxy._retry_strategy.classify_error = MagicMock(return_value=(True, 450))
        proxy._retry_strategy.should_retry = MagicMock(return_value=True)
        proxy._retry_strategy.calculate_delay = MagicMock(return_value=60)
        proxy._retry_strategy.max_retries = 5
        proxy._tables["tenants"].get = AsyncMock(return_value={"name": "Test"})
        proxy._tables["messages"].update_payload = AsyncMock()
        proxy._tables["message_events"].add_event = AsyncMock()
        return proxy

    @pytest.fixture
    def sender(self, mock_proxy):
        s = SmtpSender(mock_proxy)
        s.rate_limiter.check_and_plan = AsyncMock(return_value=(None, False))
        s.rate_limiter.release_slot = AsyncMock()
        s._resolve_account = AsyncMock(
            return_value=("smtp.test.com", 587, "u", "p", {"id": "a1", "use_tls": True})
        )
        return s

    async def test_smtp_retry_updates_message_payload_not_entry(self, sender, mock_proxy):
        entry = {
            "pk": "pk-1",
            "id": "msg-1",
            "tenant_id": "t1",
            "account_id": "a1",
            "message": {
                "from": "sender@test.com",
                "to": ["recipient@test.com"],
                "subject": "Test",
                "body": "Hello",
                "retry_count": 1,
            },
        }
        smtp = AsyncMock()
        smtp.__aenter__ = AsyncMock(return_value=smtp)
        smtp.__aexit__ = AsyncMock(return_value=None)
        smtp.send_message = AsyncMock(side_effect=Exception("450 try later"))
        sender.pool.connection = MagicMock(return_value=smtp)

        result = await sender._send_with_limits(
            EmailMessage(), None, "pk-1", "msg-1", entry
        )

        assert result["status"] == "deferred"
        assert result["retry_count"] == 2
        updated = mock_proxy._tables["messages"].update_payload.call_args[0][1]
        assert updated["retry_count"] == 2
        assert updated["from"] == "sender@test.com"
        assert "message" not in updated
        assert "pk" not in updated
