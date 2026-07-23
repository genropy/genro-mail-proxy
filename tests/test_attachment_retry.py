# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Tests for transient attachment fetch failure handling."""

from unittest.mock import MagicMock

import aiohttp
import pytest

from async_mail_service.core import _is_transient_fetch_error

from .test_core import make_core


def _response_error(status):
    return aiohttp.ClientResponseError(
        request_info=MagicMock(), history=(), status=status, message="err"
    )


class FailingAttachments:
    """Attachment manager stub that fails (or succeeds) on demand."""

    def __init__(self, exc=None, result=(b"content", "file.pdf")):
        self.exc = exc
        self.result = result
        self.calls = 0

    async def fetch(self, attachment):
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return self.result

    def guess_mime(self, filename):
        return "application", "pdf"


def _message_payload(msg_id):
    return {
        "messages": [
            {
                "id": msg_id,
                "account_id": "acc",
                "from": "sender@example.com",
                "to": ["dest@example.com"],
                "subject": "Hi",
                "body": "Body",
                "attachments": [
                    {
                        "filename": "file.pdf",
                        "storage_path": "vol:some/file.pdf",
                        "fetch_mode": "endpoint",
                    }
                ],
            }
        ]
    }


@pytest.mark.parametrize("status", [400, 404, 410])
def test_permanent_fetch_statuses(status):
    assert _is_transient_fetch_error(_response_error(status)) is False


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_transient_fetch_statuses(status):
    assert _is_transient_fetch_error(_response_error(status)) is True


def test_timeout_and_unknown_are_transient():
    assert _is_transient_fetch_error(TimeoutError("fetch timed out")) is True
    assert _is_transient_fetch_error(RuntimeError("boom")) is True


@pytest.mark.asyncio
async def test_transient_fetch_failure_defers_message(tmp_path):
    """A 503 from the attachment endpoint defers the message instead of failing it."""
    core = await make_core(tmp_path)
    core.attachments = FailingAttachments(exc=_response_error(503))

    await core.handle_command("addMessages", _message_payload("msg-atc-defer"))
    await core._process_smtp_cycle()

    messages = await core.persistence.list_messages()
    assert messages[0]["error_ts"] is None, "Transient fetch failure should not set error_ts"
    assert messages[0]["deferred_ts"] is not None, "Transient fetch failure should defer message"
    assert messages[0]["message"].get("retry_count", 0) == 1


@pytest.mark.asyncio
async def test_permanent_fetch_failure_sets_error(tmp_path):
    """A 404 from the attachment endpoint fails the message immediately."""
    core = await make_core(tmp_path)
    core.attachments = FailingAttachments(exc=_response_error(404))

    await core.handle_command("addMessages", _message_payload("msg-atc-404"))
    await core._process_smtp_cycle()

    messages = await core.persistence.list_messages()
    assert messages[0]["error_ts"] is not None
    assert messages[0]["deferred_ts"] is None
    assert "Attachment fetch failed" in messages[0]["error"]


@pytest.mark.asyncio
async def test_no_data_fetch_failure_sets_error(tmp_path):
    """A fetch returning no data fails the message immediately."""
    core = await make_core(tmp_path)
    core.attachments = FailingAttachments(result=None)

    await core.handle_command("addMessages", _message_payload("msg-atc-nodata"))
    await core._process_smtp_cycle()

    messages = await core.persistence.list_messages()
    assert messages[0]["error_ts"] is not None
    assert "returned no data" in messages[0]["error"]


@pytest.mark.asyncio
async def test_transient_fetch_failure_retry_exhaustion(tmp_path):
    """Transient fetch failures fail permanently once max retries is exceeded."""
    core = await make_core(tmp_path, max_retries=2)
    core.attachments = FailingAttachments(exc=_response_error(503))

    await core.handle_command("addMessages", _message_payload("msg-atc-exhaust"))

    for attempt in range(3):
        if attempt > 0:
            await core.persistence.clear_deferred("msg-atc-exhaust")
        await core._process_smtp_cycle()
        messages = await core.persistence.list_messages()

        if attempt < 2:
            assert messages[0]["error_ts"] is None, f"Attempt {attempt}: should be deferred"
            assert messages[0]["deferred_ts"] is not None
            assert messages[0]["message"].get("retry_count", 0) == attempt + 1
        else:
            assert messages[0]["error_ts"] is not None, "Should fail after max retries"
            assert "Max retries" in messages[0]["error"]


@pytest.mark.asyncio
async def test_fetch_recovers_after_transient_failure(tmp_path):
    """A message deferred by a transient fetch failure is sent once the endpoint recovers."""
    core = await make_core(tmp_path)
    core.attachments = FailingAttachments(exc=_response_error(503))

    await core.handle_command("addMessages", _message_payload("msg-atc-recover"))
    await core._process_smtp_cycle()

    messages = await core.persistence.list_messages()
    assert messages[0]["deferred_ts"] is not None

    # Endpoint back online
    core.attachments.exc = None
    await core.persistence.clear_deferred("msg-atc-recover")
    await core._process_smtp_cycle()

    messages = await core.persistence.list_messages()
    assert messages[0]["sent_ts"] is not None, "Message should be sent after recovery"
    assert messages[0]["error_ts"] is None
