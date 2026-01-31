# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""SMTP Sender - main coordinator for email dispatch.

This module provides SmtpSender, the central component for SMTP email delivery.
It coordinates:
- Background dispatch loop for processing queued messages
- Rate limiting per account
- Email construction with attachments
- SMTP connection management via pool
- Retry logic with exponential backoff

SmtpSender is instantiated by MailProxy and accessed via proxy.smtp_sender.

Example:
    # SmtpSender is created by MailProxy
    proxy = MailProxy(db_path="mail.db")
    await proxy.start()  # Starts smtp_sender automatically

    # Direct access if needed
    proxy.smtp_sender.wake()  # Trigger immediate dispatch cycle
"""

from __future__ import annotations

import asyncio
import math
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import TYPE_CHECKING, Any

from ..entities.tenant import LargeFileAction, get_tenant_attachment_url
from .attachments import AttachmentManager
from .pool import SMTPPool
from .rate_limiter import RateLimiter
from .retry import RetryStrategy

# Enterprise Edition: Large file storage (optional)
try:
    from enterprise.mail_proxy.attachments.large_file_storage import (
        LargeFileStorage,
        LargeFileStorageError,
    )

    HAS_LARGE_FILE_STORAGE = True
except ImportError:
    HAS_LARGE_FILE_STORAGE = False
    LargeFileStorage = None  # type: ignore[misc, assignment]

    class LargeFileStorageError(Exception):  # type: ignore[no-redef]
        """Stub for CE."""

        pass


if TYPE_CHECKING:
    from tools.prometheus import MailMetrics

    from ..proxy import MailProxy


class AccountConfigurationError(RuntimeError):
    """Raised when SMTP account configuration is missing or invalid."""

    def __init__(self, message: str = "Missing SMTP account configuration"):
        super().__init__(message)
        self.code = "missing_account_configuration"


class AttachmentTooLargeError(ValueError):
    """Raised when an attachment exceeds the size limit and action is 'reject'."""

    def __init__(self, filename: str, size_mb: float, max_size_mb: float):
        self.filename = filename
        self.size_mb = size_mb
        self.max_size_mb = max_size_mb
        super().__init__(
            f"Attachment '{filename}' ({size_mb:.1f} MB) exceeds limit ({max_size_mb} MB)"
        )


class SmtpSender:
    """Central coordinator for SMTP email dispatch.

    Manages the complete email sending pipeline:
    - Fetches ready messages from database queue
    - Builds EmailMessage objects with attachments
    - Applies rate limiting per account
    - Sends via SMTP connection pool
    - Handles retries for temporary failures
    - Records delivery events

    Attributes:
        proxy: Parent MailProxy instance for accessing db, config, metrics.
        pool: SMTP connection pool for connection reuse.
        rate_limiter: Per-account rate limiting controller.
    """

    def __init__(self, proxy: MailProxy) -> None:
        """Initialize SmtpSender.

        Args:
            proxy: Parent MailProxy instance.
        """
        self.proxy = proxy
        self.pool = SMTPPool()
        self.rate_limiter = RateLimiter(self)

        # Background task handles
        self._task_dispatch: asyncio.Task | None = None
        self._task_cleanup: asyncio.Task | None = None

        # Control events
        self._stop = asyncio.Event()
        self._wake_event = asyncio.Event()
        self._wake_cleanup_event = asyncio.Event()

        # Per-account concurrency semaphores
        self._account_semaphores: dict[str, asyncio.Semaphore] = {}

    # ----------------------------------------------------------------- properties
    @property
    def db(self):
        """Database access via proxy."""
        return self.proxy.db

    @property
    def config(self):
        """Configuration via proxy."""
        return self.proxy.config

    @property
    def logger(self):
        """Logger via proxy."""
        return self.proxy.logger

    @property
    def metrics(self) -> MailMetrics:
        """Prometheus metrics via proxy."""
        return self.proxy.metrics

    @property
    def attachments(self) -> AttachmentManager:
        """Global attachment manager via proxy."""
        return self.proxy.attachments

    @property
    def _retry_strategy(self) -> RetryStrategy:
        """Retry strategy via proxy."""
        return self.proxy._retry_strategy

    @property
    def _test_mode(self) -> bool:
        """Test mode flag via proxy."""
        return self.proxy._test_mode

    @property
    def _send_loop_interval(self) -> float:
        """Send loop interval via proxy."""
        return self.proxy._send_loop_interval

    @property
    def _smtp_batch_size(self) -> int:
        """SMTP batch size via proxy."""
        return self.proxy._smtp_batch_size

    @property
    def _batch_size_per_account(self) -> int:
        """Per-account batch size via proxy."""
        return self.proxy._batch_size_per_account

    @property
    def _max_concurrent_sends(self) -> int:
        """Max concurrent sends via proxy."""
        return self.proxy._max_concurrent_sends

    @property
    def _max_concurrent_per_account(self) -> int:
        """Max concurrent per account via proxy."""
        return self.proxy._max_concurrent_per_account

    @property
    def _max_concurrent_attachments(self) -> int:
        """Max concurrent attachments via proxy."""
        return self.proxy._max_concurrent_attachments

    @property
    def _attachment_timeout(self) -> float:
        """Attachment timeout via proxy."""
        return self.proxy._attachment_timeout

    @property
    def _attachment_semaphore(self) -> asyncio.Semaphore | None:
        """Attachment semaphore via proxy."""
        return self.proxy._attachment_semaphore

    @property
    def _attachment_cache(self):
        """Attachment cache via proxy."""
        return self.proxy._attachment_cache

    @property
    def _log_delivery_activity(self) -> bool:
        """Log delivery activity flag via proxy."""
        return self.proxy._log_delivery_activity

    @property
    def default_host(self) -> str | None:
        """Default SMTP host via proxy."""
        return self.proxy.default_host

    @property
    def default_port(self) -> int | None:
        """Default SMTP port via proxy."""
        return self.proxy.default_port

    @property
    def default_user(self) -> str | None:
        """Default SMTP user via proxy."""
        return self.proxy.default_user

    @property
    def default_password(self) -> str | None:
        """Default SMTP password via proxy."""
        return self.proxy.default_password

    @property
    def default_use_tls(self) -> bool | None:
        """Default use TLS via proxy."""
        return self.proxy.default_use_tls

    # ----------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        """Start background dispatch and cleanup loops."""
        self._stop.clear()
        self.logger.debug("Starting SmtpSender dispatch loop...")
        self._task_dispatch = asyncio.create_task(self._dispatch_loop(), name="smtp-dispatch-loop")
        if not self._test_mode:
            self.logger.debug("Starting SmtpSender cleanup loop...")
            self._task_cleanup = asyncio.create_task(self._cleanup_loop(), name="smtp-cleanup-loop")

    async def stop(self) -> None:
        """Stop all background tasks gracefully."""
        self._stop.set()
        self._wake_event.set()
        self._wake_cleanup_event.set()
        tasks = [t for t in [self._task_dispatch, self._task_cleanup] if t]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def wake(self) -> None:
        """Wake the dispatch loop for immediate processing."""
        self._wake_event.set()

    # ----------------------------------------------------------------- dispatch loop
    async def _dispatch_loop(self) -> None:
        """Background loop that continuously processes queued messages.

        Runs until stop() is called, fetching ready messages from the database
        and attempting SMTP delivery.
        """
        self.logger.debug("SMTP dispatch loop started")
        first_iteration = True
        while not self._stop.is_set():
            if first_iteration and self._test_mode:
                self.logger.info("First iteration in test mode, waiting for wakeup")
                await self._wait_for_wakeup(self._send_loop_interval)
            first_iteration = False
            try:
                self.logger.debug("Processing SMTP cycle...")
                processed = await self._process_cycle()
                self.logger.debug(f"SMTP cycle processed={processed}")
                # If messages were sent, trigger immediate client report sync
                if processed:
                    self.logger.debug("Messages sent, triggering client report sync")
                    self.proxy.client_reporter._wake_event.set()
            except Exception as exc:  # pragma: no cover - defensive
                self.logger.exception("Unhandled error in SMTP dispatch loop: %s", exc)
                processed = False
            if not processed:
                self.logger.debug(f"No messages processed, waiting {self._send_loop_interval}s")
                await self._wait_for_wakeup(self._send_loop_interval)

    async def _process_cycle(self) -> bool:
        """Execute one SMTP dispatch cycle with priority-aware parallel dispatch.

        Immediate priority messages (priority=0) are always fetched and processed
        first, followed by regular messages.

        Returns:
            True if any messages were processed, False otherwise.
        """
        now_ts = self._utc_now_epoch()
        processed_any = False

        # First, process immediate priority messages (priority=0)
        immediate_batch = await self.db.table("messages").fetch_ready(
            limit=self._smtp_batch_size, now_ts=now_ts, priority=0
        )
        if immediate_batch:
            self.logger.debug(f"Processing {len(immediate_batch)} immediate priority messages")
            await self._dispatch_batch(immediate_batch, now_ts)
            processed_any = True

        # Then, process regular priority messages (priority >= 1)
        regular_batch = await self.db.table("messages").fetch_ready(
            limit=self._smtp_batch_size, now_ts=now_ts, min_priority=1
        )
        if regular_batch:
            self.logger.debug(f"Processing {len(regular_batch)} regular priority messages")
            await self._dispatch_batch(regular_batch, now_ts)
            processed_any = True

        await self.proxy._refresh_queue_gauge()
        return processed_any

    async def _dispatch_batch(self, batch: list[dict[str, Any]], now_ts: int) -> None:
        """Dispatch a batch of messages in parallel with concurrency limits.

        Groups messages by account and applies per-account batch limits
        before dispatching in parallel.

        Args:
            batch: List of message entries to dispatch.
            now_ts: Current UTC timestamp.
        """
        # Group messages by account_id and apply per-account batch limit
        messages_by_account: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for entry in batch:
            # account_id is at entry level from fetch_ready, not in message payload
            account_id = entry.get("account_id") or "default"
            messages_by_account[account_id].append(entry)

        # Collect all messages to send, respecting per-account batch limits
        all_messages_to_send: list[tuple[dict, str]] = []

        for account_id, account_messages in messages_by_account.items():
            # Get account-specific batch_size if available
            account_batch_size = self._batch_size_per_account
            if account_id and account_id != "default" and account_messages:
                tenant_id = account_messages[0].get("tenant_id")
                if tenant_id:
                    try:
                        account_data = await self.db.table("accounts").get(tenant_id, account_id)
                        if account_data and account_data.get("batch_size"):
                            account_batch_size = int(account_data["batch_size"])
                    except Exception:
                        pass  # Fall back to global default

            # Limit messages for this account to its batch_size
            messages_to_send = account_messages[:account_batch_size]
            skipped_count = len(account_messages) - len(messages_to_send)

            if skipped_count > 0:
                self.logger.info(
                    f"Account {account_id}: processing {len(messages_to_send)} messages, "
                    f"deferring {skipped_count} to next cycle (batch_size={account_batch_size})"
                )

            for entry in messages_to_send:
                all_messages_to_send.append((entry, account_id))

        if not all_messages_to_send:
            return

        # Global semaphore to limit overall concurrency
        global_semaphore = asyncio.Semaphore(self._max_concurrent_sends)

        async def dispatch_with_limits(entry: dict, account_id: str) -> None:
            """Dispatch a single message respecting concurrency limits."""
            account_semaphore = self._get_account_semaphore(account_id)
            async with global_semaphore, account_semaphore:
                self.logger.debug(f"Dispatching message {entry.get('id')} for account {account_id}")
                await self._dispatch_message(entry, now_ts)

        # Dispatch all messages in parallel with concurrency limits
        tasks = [dispatch_with_limits(entry, acc_id) for entry, acc_id in all_messages_to_send]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Log any exceptions that occurred
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                entry, account_id = all_messages_to_send[i]
                self.logger.exception(
                    f"Unexpected error dispatching message {entry.get('id')} for account {account_id}: {result}"
                )

    def _get_account_semaphore(self, account_id: str) -> asyncio.Semaphore:
        """Get or create a semaphore for per-account concurrency limiting."""
        if account_id not in self._account_semaphores:
            self._account_semaphores[account_id] = asyncio.Semaphore(
                self._max_concurrent_per_account
            )
        return self._account_semaphores[account_id]

    async def _dispatch_message(self, entry: dict[str, Any], now_ts: int) -> None:
        """Attempt to deliver a single message via SMTP.

        Builds the email, resolves the SMTP account, applies rate limits,
        and performs the actual send. Updates message status based on outcome.

        Args:
            entry: Message entry dict with pk, id, message payload, and metadata.
            now_ts: Current UTC timestamp for error/sent timestamp recording.
        """
        pk = entry.get("pk")
        msg_id = entry.get("id")
        message = entry.get("message") or {}
        if self._log_delivery_activity:
            recipients_preview = self._summarise_addresses(message.get("to"))
            self.logger.info(
                "Attempting delivery for message %s to %s (account=%s)",
                msg_id or "-",
                recipients_preview,
                message.get("account_id") or "default",
            )
        if pk:
            await self.db.table("messages").clear_deferred(pk)
        try:
            email_msg, envelope_from = await self._build_email(message)
        except KeyError as exc:
            reason = f"missing {exc}"
            if pk:
                await self.db.table("message_events").add_event(
                    pk, "error", now_ts, description=reason
                )
            await self._publish_result(
                {
                    "id": msg_id,
                    "status": "error",
                    "error": reason,
                    "timestamp": self._utc_now_iso(),
                    "account": message.get("account_id"),
                }
            )
            return
        except ValueError as exc:
            reason = str(exc)
            if pk:
                await self.db.table("message_events").add_event(
                    pk, "error", now_ts, description=reason
                )
            await self._publish_result(
                {
                    "id": msg_id,
                    "status": "error",
                    "error": reason,
                    "timestamp": self._utc_now_iso(),
                    "account": message.get("account_id"),
                }
            )
            return

        # Pass entry (with tenant_id, account_id at top level) not just message payload
        event = await self._send_with_limits(email_msg, envelope_from, pk, msg_id, entry)
        if event:
            await self._publish_result(event)

    async def _send_with_limits(
        self,
        msg: EmailMessage,
        envelope_from: str | None,
        pk: str | None,
        msg_id: str | None,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Send an email message with rate limiting and retry logic.

        Args:
            msg: Constructed EmailMessage ready for sending.
            envelope_from: SMTP envelope sender address.
            pk: Internal primary key for database updates (UUID string).
            msg_id: Message ID for tracking and event recording.
            payload: Original message payload with retry state.

        Returns:
            Event dict describing the outcome (sent/error/deferred), or None
            if the message was deferred due to rate limiting.
        """
        tenant_id = payload.get("tenant_id") or ""
        account_id = payload.get("account_id")

        # Fetch tenant name for metrics
        tenant_name = tenant_id
        if tenant_id:
            tenant = await self.db.table("tenants").get(tenant_id)
            if tenant:
                tenant_name = tenant.get("name") or tenant_id

        try:
            host, port, user, password, acc = await self._resolve_account(tenant_id, account_id)
        except AccountConfigurationError as exc:
            error_ts = self._utc_now_epoch()
            if pk:
                await self.db.table("message_events").add_event(
                    pk, "error", error_ts, description=str(exc)
                )
            return {
                "id": msg_id,
                "status": "error",
                "error": str(exc),
                "error_code": exc.code,
                "timestamp": self._utc_now_iso(),
                "account": account_id or "default",
            }

        use_tls = acc.get("use_tls")
        use_tls = int(port) == 465 if use_tls is None else bool(use_tls)
        resolved_account_id = account_id or acc.get("id") or "default"

        # Prepare metrics labels
        metric_labels = {
            "tenant_id": tenant_id,
            "tenant_name": tenant_name,
            "account_id": resolved_account_id,
            "account_name": resolved_account_id,
        }

        deferred_until, should_reject = await self.rate_limiter.check_and_plan(acc)
        if deferred_until:
            self.metrics.inc_rate_limited(**metric_labels)

            if should_reject:
                self.logger.info(
                    "Message %s rate-limited and rejected for account %s",
                    msg_id,
                    resolved_account_id,
                )
                error_ts = self._utc_now_epoch()
                if pk:
                    await self.db.table("message_events").add_event(
                        pk, "error", error_ts, description="rate_limit_exceeded"
                    )
                return {
                    "id": msg_id,
                    "status": "error",
                    "error": "rate_limit_exceeded",
                    "error_code": 429,
                    "timestamp": self._utc_now_iso(),
                    "account": resolved_account_id,
                }

            # Rate limit hit - defer message for later retry
            if pk:
                now_ts = self._utc_now_epoch()
                await self.db.table("message_events").add_event(
                    pk,
                    "deferred",
                    now_ts,
                    description="rate_limit",
                    metadata={"deferred_ts": deferred_until},
                )
            self.metrics.inc_deferred(**metric_labels)
            self.logger.debug(
                "Message %s rate-limited for account %s, deferred until %s",
                msg_id,
                resolved_account_id,
                deferred_until,
            )
            return None  # No result to report, message will be retried later

        try:
            async with self.pool.connection(host, port, user, password, use_tls=use_tls) as smtp:
                envelope_sender = envelope_from or msg.get("From")
                await asyncio.wait_for(smtp.send_message(msg, sender=envelope_sender), timeout=30.0)
        except Exception as exc:
            # Release the rate limiter slot since send failed
            await self.rate_limiter.release_slot(resolved_account_id)

            # Classify the error and get retry count
            is_temporary, smtp_code = self._retry_strategy.classify_error(exc)
            retry_count = payload.get("retry_count", 0)

            # Determine if we should retry
            should_retry = self._retry_strategy.should_retry(retry_count, exc)

            if should_retry:
                delay = self._retry_strategy.calculate_delay(retry_count)
                now_ts = self._utc_now_epoch()
                deferred_until = now_ts + delay

                updated_payload = dict(payload)
                updated_payload["retry_count"] = retry_count + 1

                error_info = f"{exc} (SMTP {smtp_code})" if smtp_code else str(exc)
                if pk:
                    await self.db.table("messages").update_payload(pk, updated_payload)
                    await self.db.table("message_events").add_event(
                        pk,
                        "deferred",
                        now_ts,
                        description=error_info,
                        metadata={"deferred_ts": deferred_until, "retry_count": retry_count + 1},
                    )
                self.metrics.inc_deferred(**metric_labels)

                max_retries = self._retry_strategy.max_retries
                self.logger.warning(
                    "Temporary error for message %s (attempt %d/%d): %s - retrying in %ds",
                    msg_id,
                    retry_count + 1,
                    max_retries,
                    error_info,
                    delay,
                )

                return {
                    "id": msg_id,
                    "status": "deferred",
                    "deferred_until": deferred_until,
                    "error": error_info,
                    "retry_count": retry_count + 1,
                    "timestamp": self._utc_now_iso(),
                    "account": resolved_account_id,
                }
            else:
                error_ts = self._utc_now_epoch()
                error_info = f"{exc} (SMTP {smtp_code})" if smtp_code else str(exc)
                max_retries = self._retry_strategy.max_retries

                if retry_count >= max_retries:
                    error_info = f"Max retries ({max_retries}) exceeded: {error_info}"
                    self.logger.error(
                        "Message %s failed permanently after %d attempts: %s",
                        msg_id,
                        retry_count,
                        error_info,
                    )
                else:
                    self.logger.error(
                        "Message %s failed with permanent error: %s",
                        msg_id,
                        error_info,
                    )

                if pk:
                    await self.db.table("message_events").add_event(
                        pk,
                        "error",
                        error_ts,
                        description=error_info,
                        metadata={"smtp_code": smtp_code, "retry_count": retry_count},
                    )
                self.metrics.inc_error(**metric_labels)

                return {
                    "id": msg_id,
                    "status": "error",
                    "error": error_info,
                    "smtp_code": smtp_code,
                    "retry_count": retry_count,
                    "timestamp": self._utc_now_iso(),
                    "account": resolved_account_id,
                }

        sent_ts = self._utc_now_epoch()
        if pk:
            await self.db.table("message_events").add_event(pk, "sent", sent_ts)
        await self.rate_limiter.log_send(resolved_account_id)
        self.metrics.inc_sent(**metric_labels)
        return {
            "id": msg_id,
            "status": "sent",
            "timestamp": self._utc_now_iso(),
            "account": resolved_account_id,
        }

    async def _resolve_account(
        self, tenant_id: str, account_id: str | None
    ) -> tuple[str, int, str | None, str | None, dict[str, Any]]:
        """Resolve SMTP connection parameters for a message.

        Args:
            tenant_id: Tenant ID that owns the account.
            account_id: Account ID to look up, or None to use defaults.

        Returns:
            Tuple of (host, port, user, password, account_dict).

        Raises:
            AccountConfigurationError: If no account found and no defaults.
        """
        if account_id:
            try:
                acc = await self.db.table("accounts").get(tenant_id, account_id)
                return acc["host"], int(acc["port"]), acc.get("user"), acc.get("password"), acc
            except ValueError as e:
                raise AccountConfigurationError(str(e)) from e
        if self.default_host and self.default_port:
            return (
                self.default_host,
                int(self.default_port),
                self.default_user,
                self.default_password,
                {"id": "default", "use_tls": self.default_use_tls},
            )
        raise AccountConfigurationError()

    # ----------------------------------------------------------------- email building
    async def _build_email(self, data: dict[str, Any]) -> tuple[EmailMessage, str]:
        """Build an EmailMessage from a message payload.

        Constructs headers (From, To, Cc, Bcc, Subject, etc.), sets the body
        content with appropriate MIME type, and fetches/attaches any attachments.

        Args:
            data: Message payload with from, to, subject, body, attachments, etc.

        Returns:
            Tuple of (EmailMessage, envelope_sender_address).

        Raises:
            KeyError: If required fields (from, to) are missing.
            ValueError: If attachment fetching fails.
            AttachmentTooLargeError: If attachment exceeds limit and action is 'reject'.
        """

        def _format_addresses(value: Any) -> str | None:
            if not value:
                return None
            if isinstance(value, str):
                items = [part.strip() for part in value.split(",") if part.strip()]
                return ", ".join(items) if items else None
            if isinstance(value, (list, tuple, set)):
                items = [str(addr).strip() for addr in value if addr]
                return ", ".join(items) if items else None
            return str(value)

        msg = EmailMessage()
        msg["From"] = data["from"]
        to_value = _format_addresses(data.get("to"))
        if not to_value:
            raise KeyError("to")
        msg["To"] = to_value
        msg["Subject"] = data["subject"]
        if cc_value := _format_addresses(data.get("cc")):
            msg["Cc"] = cc_value
        if bcc_value := _format_addresses(data.get("bcc")):
            msg["Bcc"] = bcc_value
        if reply_to := data.get("reply_to"):
            msg["Reply-To"] = reply_to
        if message_id := data.get("message_id"):
            msg["Message-ID"] = message_id
        envelope_from = data.get("return_path") or data["from"]
        content_subtype = "html" if data.get("content_type", "plain") == "html" else "plain"
        body_content = data.get("body", "")
        msg.set_content(body_content, subtype=content_subtype)
        for header, value in (data.get("headers") or {}).items():
            if value is None:
                continue
            value_str = str(value)
            if header in msg:
                msg.replace_header(header, value_str)
            else:
                msg[header] = value_str

        # Add tracking header for bounce detection correlation
        if msg_id := data.get("id"):
            msg["X-Genro-Mail-ID"] = msg_id

        attachments = data.get("attachments", []) or []
        if attachments:
            await self._process_attachments(msg, data, attachments, content_subtype)

        return msg, envelope_from

    async def _process_attachments(
        self,
        msg: EmailMessage,
        data: dict[str, Any],
        attachments: list[dict[str, Any]],
        content_subtype: str,
    ) -> None:
        """Process and attach files to the email message.

        Args:
            msg: EmailMessage to add attachments to.
            data: Original message payload.
            attachments: List of attachment specifications.
            content_subtype: 'html' or 'plain' for body type.
        """
        # Get tenant configuration for large file handling
        large_file_config = await self._get_large_file_config_for_message(data)
        large_file_storage = None
        if large_file_config and large_file_config.get("enabled"):
            large_file_storage = self._create_large_file_storage(large_file_config)

        # Determine which attachment manager to use
        attachment_manager = await self._get_attachment_manager_for_message(data)
        results = await asyncio.gather(
            *[self._fetch_attachment_with_timeout(att, attachment_manager) for att in attachments],
            return_exceptions=True,
        )

        # Track attachments that were converted to download links
        rewritten_attachments: list[dict[str, Any]] = []

        for att, result in zip(attachments, results, strict=True):
            filename = att.get("filename", "file.bin")
            if isinstance(result, Exception):
                self.logger.error("Failed to fetch attachment %s: %s", filename, result)
                raise ValueError(f"Attachment fetch failed for {filename}: {result}")
            if result is None:
                self.logger.error("Attachment without data (filename=%s)", filename)
                raise ValueError(f"Attachment {filename} returned no data")
            content, resolved_filename = result
            size_mb = len(content) / (1024 * 1024)

            # Check if we need to handle this as a large file
            should_rewrite = False
            if large_file_config and large_file_config.get("enabled"):
                max_size_mb = large_file_config.get("max_size_mb", 10.0)
                action = large_file_config.get("action", "warn")

                if size_mb > max_size_mb:
                    if action == LargeFileAction.REJECT.value:
                        raise AttachmentTooLargeError(resolved_filename, size_mb, max_size_mb)
                    elif action == LargeFileAction.REWRITE.value and large_file_storage:
                        should_rewrite = True
                    else:  # warn
                        self.logger.warning(
                            "Large attachment %s (%.1f MB) exceeds limit (%.1f MB) - sending anyway",
                            resolved_filename,
                            size_mb,
                            max_size_mb,
                        )

            if should_rewrite and large_file_storage:
                # Upload to external storage and generate download link
                file_id = str(uuid.uuid4())
                try:
                    await large_file_storage.upload(file_id, content, resolved_filename)
                    ttl_days = large_file_config.get("file_ttl_days", 30)
                    download_url = large_file_storage.get_download_url(
                        file_id, resolved_filename, expires_in=ttl_days * 86400
                    )
                    rewritten_attachments.append(
                        {
                            "filename": resolved_filename,
                            "size_mb": size_mb,
                            "url": download_url,
                        }
                    )
                    self.logger.info(
                        "Large attachment %s (%.1f MB) uploaded to storage",
                        resolved_filename,
                        size_mb,
                    )
                except LargeFileStorageError as e:
                    self.logger.error(
                        "Failed to upload large attachment %s: %s - attaching normally",
                        resolved_filename,
                        e,
                    )
                    should_rewrite = False

            if not should_rewrite:
                # Normal attachment: add to email
                mime_type_override = att.get("mime_type")
                if mime_type_override and "/" in mime_type_override:
                    maintype, subtype = mime_type_override.split("/", 1)
                else:
                    maintype, subtype = self.attachments.guess_mime(resolved_filename)
                msg.add_attachment(
                    content, maintype=maintype, subtype=subtype, filename=resolved_filename
                )

        # If we have rewritten attachments, append download links to the body
        if rewritten_attachments:
            self._append_download_links_to_email(msg, rewritten_attachments, content_subtype)

    def _append_download_links_to_email(
        self,
        msg: EmailMessage,
        rewritten_attachments: list[dict[str, Any]],
        content_subtype: str,
    ) -> None:
        """Append download links for rewritten attachments to the email body."""
        if content_subtype == "html":
            links_html = []
            for att in rewritten_attachments:
                links_html.append(
                    f'<li><a href="{att["url"]}">{att["filename"]}</a> '
                    f"({att['size_mb']:.1f} MB)</li>"
                )
            footer = (
                '<hr style="margin-top: 20px;">'
                "<p><strong>Large attachments available for download:</strong></p>"
                f"<ul>{''.join(links_html)}</ul>"
                "<p><em>Links will expire after the configured retention period.</em></p>"
            )
        else:
            links_text = []
            for att in rewritten_attachments:
                links_text.append(f"  - {att['filename']} ({att['size_mb']:.1f} MB): {att['url']}")
            footer = (
                "\n\n---\n"
                "Large attachments available for download:\n"
                f"{chr(10).join(links_text)}\n"
                "(Links will expire after the configured retention period.)"
            )

        # Get current body and append footer
        if msg.is_multipart():
            body_part = msg.get_body(
                preferencelist=("html", "plain") if content_subtype == "html" else ("plain", "html")
            )
            if body_part is not None:
                current_body = body_part.get_content()
                new_body = current_body + footer
                body_part.set_content(new_body, subtype=content_subtype)
            else:
                self.logger.warning("No body part found in multipart message")
                msg.add_alternative(footer, subtype=content_subtype)
        else:
            current_body = msg.get_content()
            new_body = current_body + footer
            msg.set_content(new_body, subtype=content_subtype)

    async def _get_large_file_config_for_message(
        self, data: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Get the large file configuration for a message's tenant."""
        tenant_id = data.get("tenant_id")
        if not tenant_id:
            return None
        tenant = await self.db.table("tenants").get(tenant_id)
        if not tenant:
            return None
        return tenant.get("large_file_config")

    def _create_large_file_storage(self, config: dict[str, Any]) -> LargeFileStorage | None:
        """Create a LargeFileStorage instance from tenant config."""
        storage_url = config.get("storage_url")
        if not storage_url:
            return None
        return LargeFileStorage(
            storage_url=storage_url,
            public_base_url=config.get("public_base_url"),
        )

    async def _get_attachment_manager_for_message(self, data: dict[str, Any]) -> AttachmentManager:
        """Get the appropriate AttachmentManager for a message."""
        tenant_id = data.get("tenant_id")
        if not tenant_id:
            return self.attachments

        tenant = await self.db.table("tenants").get(tenant_id)
        if not tenant:
            return self.attachments

        # Check if tenant has custom attachment settings
        tenant_attachment_url = get_tenant_attachment_url(tenant)
        tenant_auth = tenant.get("client_auth")

        # Get tenant's storage manager for mount:path resolution
        storage_manager = None
        try:
            storages_table = self.db.table("storages")
            storage_manager = await storages_table.get_storage_manager(tenant_id)
        except (ValueError, KeyError):
            pass  # No storages configured for tenant

        if not tenant_attachment_url and not tenant_auth and not storage_manager:
            return self.attachments

        # Build http_auth_config from tenant's auth config
        http_auth_config = None
        if tenant_auth:
            http_auth_config = {
                "method": tenant_auth.get("method", "none"),
                "token": tenant_auth.get("token"),
                "user": tenant_auth.get("user"),
                "password": tenant_auth.get("password"),
            }

        return AttachmentManager(
            storage_manager=storage_manager,
            http_endpoint=tenant_attachment_url,
            http_auth_config=http_auth_config,
            cache=self._attachment_cache,
        )

    async def _fetch_attachment_with_timeout(
        self,
        att: dict[str, Any],
        attachment_manager: AttachmentManager | None = None,
    ) -> tuple[bytes, str] | None:
        """Fetch an attachment using the configured timeout budget."""
        manager = attachment_manager or self.attachments
        semaphore = self._attachment_semaphore or asyncio.Semaphore(
            self._max_concurrent_attachments
        )
        async with semaphore:
            try:
                result = await asyncio.wait_for(
                    manager.fetch(att), timeout=self._attachment_timeout
                )
            except asyncio.TimeoutError as exc:
                raise TimeoutError(
                    f"Attachment {att.get('filename', 'file.bin')} fetch timed out"
                ) from exc
            return result

    # ----------------------------------------------------------------- cleanup loop
    async def _cleanup_loop(self) -> None:
        """Background loop that maintains SMTP connection pool health."""
        cleanup_interval = 150  # seconds
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._wake_cleanup_event.wait(), timeout=cleanup_interval)
                self._wake_cleanup_event.clear()
            except asyncio.TimeoutError:
                pass
            if self._stop.is_set():
                break
            await self.pool.cleanup()

    # ----------------------------------------------------------------- utilities
    async def _wait_for_wakeup(self, timeout: float | None) -> None:
        """Pause the dispatch loop until timeout or wake event."""
        self.logger.debug(f"_wait_for_wakeup called with timeout={timeout}")
        if self._stop.is_set():
            self.logger.debug("_stop is set, returning immediately")
            return
        if timeout is None:
            self.logger.debug("Waiting indefinitely for wake event")
            await self._wake_event.wait()
            self._wake_event.clear()
            return
        timeout = float(timeout)
        if math.isinf(timeout):
            self.logger.debug("Infinite timeout, waiting for wake event")
            await self._wake_event.wait()
            self._wake_event.clear()
            return
        timeout = max(0.0, timeout)
        if timeout == 0:
            self.logger.debug("Zero timeout, yielding")
            await asyncio.sleep(0)
            return
        self.logger.debug(f"Waiting {timeout}s for wake event or timeout")
        try:
            await asyncio.wait_for(self._wake_event.wait(), timeout=timeout)
            self.logger.debug("Woken up by event")
        except asyncio.TimeoutError:
            self.logger.debug(f"Timeout after {timeout}s")
            return
        self._wake_event.clear()

    async def _publish_result(self, event: dict[str, Any]) -> None:
        """Publish a delivery event to the result queue."""
        await self.proxy._publish_result(event)

    @staticmethod
    def _utc_now_iso() -> str:
        """Return the current UTC timestamp as ISO-8601 string."""
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _utc_now_epoch() -> int:
        """Return the current UTC timestamp as seconds since Unix epoch."""
        return int(datetime.now(timezone.utc).timestamp())

    @staticmethod
    def _summarise_addresses(value: Any) -> str:
        """Create a compact string summary of email addresses for logging."""
        if not value:
            return "-"
        if isinstance(value, str):
            items = [part.strip() for part in value.split(",") if part.strip()]
        elif isinstance(value, (list, tuple, set)):
            items = [str(item).strip() for item in value if item]
        else:
            items = [str(value).strip()]
        preview = ", ".join(item for item in items if item)
        if len(preview) > 200:
            return f"{preview[:197]}..."
        return preview or "-"


__all__ = ["SmtpSender", "AccountConfigurationError", "AttachmentTooLargeError"]
