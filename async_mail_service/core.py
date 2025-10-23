"""Core orchestration logic for the asynchronous mail dispatcher."""

from __future__ import annotations

import asyncio
import math
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Tuple

import aiohttp
import aiosmtplib

from .attachments import AttachmentManager
from .logger import get_logger
from .persistence import Persistence
from .prometheus import MailMetrics
from .rate_limit import RateLimiter
from .smtp_pool import SMTPPool
from zoneinfo import ZoneInfo

PRIORITY_LABELS = {
    0: "immediate",
    1: "high",
    2: "medium",
    3: "low",
}
LABEL_TO_PRIORITY = {label: value for value, label in PRIORITY_LABELS.items()}
DEFAULT_PRIORITY = 2

# Default retry configuration
DEFAULT_MAX_RETRIES = 5
DEFAULT_RETRY_DELAYS = [60, 300, 900, 3600, 7200]  # 1min, 5min, 15min, 1h, 2h


class AccountConfigurationError(RuntimeError):
    """Raised when a message is missing the information required to resolve an SMTP account."""

    def __init__(self, message: str = "Missing SMTP account configuration"):
        super().__init__(message)
        self.code = "missing_account_configuration"


def _classify_smtp_error(exc: Exception) -> tuple[bool, Optional[int]]:
    """
    Classify an SMTP error as temporary or permanent.

    Returns:
        tuple: (is_temporary, smtp_code)
            - is_temporary: True if the error should trigger a retry
            - smtp_code: The SMTP error code if available, None otherwise
    """
    # Extract SMTP code from aiosmtplib exceptions
    smtp_code = None
    if isinstance(exc, aiosmtplib.SMTPException):
        # aiosmtplib stores code in different attributes depending on exception type
        smtp_code = getattr(exc, 'smtp_code', None) or getattr(exc, 'code', None)

    # Network/timeout errors are temporary
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, ConnectionError, OSError)):
        return True, smtp_code

    # SMTP-specific temporary errors (4xx codes)
    if smtp_code:
        # 4xx codes are temporary failures
        if 400 <= smtp_code < 500:
            return True, smtp_code
        # 5xx codes are permanent failures
        if 500 <= smtp_code < 600:
            return False, smtp_code

    # Check error message for common temporary error patterns
    error_msg = str(exc).lower()
    temporary_patterns = [
        '421',  # Service not available
        '450',  # Mailbox unavailable
        '451',  # Local error in processing
        '452',  # Insufficient system storage
        'timeout',
        'connection refused',
        'connection reset',
        'temporarily unavailable',
        'try again',
        'throttl',  # throttled/throttling
    ]
    for pattern in temporary_patterns:
        if pattern in error_msg:
            return True, smtp_code

    # Default: treat unknown errors as temporary (safer for retry)
    return True, smtp_code


def _calculate_retry_delay(retry_count: int, delays: List[int] = None) -> int:
    """
    Calculate the delay in seconds before the next retry attempt.

    Args:
        retry_count: Number of previous retry attempts (0-indexed)
        delays: Optional list of delays in seconds for each retry

    Returns:
        Delay in seconds before next retry
    """
    if delays is None:
        delays = DEFAULT_RETRY_DELAYS
    if retry_count >= len(delays):
        # Use the last delay for all subsequent retries
        return delays[-1]
    return delays[retry_count]


class AsyncMailCore:
    """Coordinate scheduling, rate limiting, persistence and delivery."""

    def __init__(
        self,
        *,
        db_path: str | None = "/data/mail_service.db",
        logger=None,
        metrics: MailMetrics | None = None,
        start_active: bool = False,
        timezone: str = "Europe/Rome",
        result_queue_size: int = 1000,
        delivery_queue_size: int = 1000,  # legacy arg kept for compatibility
        message_queue_size: int = 10000,
        queue_put_timeout: float = 5.0,
        max_enqueue_batch: int = 1000,
        attachment_timeout: int = 30,
        client_sync_url: str | None = None,
        client_sync_user: str | None = None,
        client_sync_password: str | None = None,
        client_sync_token: str | None = None,
        default_priority: int | str = DEFAULT_PRIORITY,
        report_delivery_callable: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        send_loop_interval: float = 0.5,
        report_retention_seconds: int | None = None,
        batch_size_per_account: int = 50,
        test_mode: bool = False,
        log_delivery_activity: bool = False,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_delays: Optional[List[int]] = None,
    ):
        """Prepare the runtime collaborators and scheduler state."""
        self.default_host = None
        self.default_port = None
        self.default_user = None
        self.default_password = None
        self.default_use_tls = False

        self.logger = logger or get_logger()
        self.pool = SMTPPool()
        self.persistence = Persistence(db_path or ":memory:")
        self.rate_limiter = RateLimiter(self.persistence)
        self.metrics = metrics or MailMetrics()
        self.timezone = ZoneInfo(timezone)
        self._queue_put_timeout = queue_put_timeout
        self._max_enqueue_batch = max_enqueue_batch
        self._attachment_timeout = attachment_timeout
        base_send_interval = max(0.05, float(send_loop_interval))
        self._smtp_batch_size = max(1, int(message_queue_size))
        self._report_retention_seconds = (
            report_retention_seconds if report_retention_seconds is not None else 7 * 24 * 3600
        )
        self._test_mode = bool(test_mode)

        self._stop = asyncio.Event()
        self._active = start_active
        self._rules: List[Dict[str, Any]] = []

        self._send_loop_interval = math.inf if self._test_mode else base_send_interval
        self._wake_event = asyncio.Event()  # Wake event for SMTP dispatch loop
        self._wake_client_event = asyncio.Event()  # Wake event for client report loop
        self._result_queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue(maxsize=result_queue_size)
        self._task_smtp: Optional[asyncio.Task] = None
        self._task_client: Optional[asyncio.Task] = None
        self._task_cleanup: Optional[asyncio.Task] = None

        self._client_sync_url = client_sync_url
        self._client_sync_user = client_sync_user
        self._client_sync_password = client_sync_password
        self._client_sync_token = client_sync_token
        self._report_delivery_callable = report_delivery_callable

        self.attachments = AttachmentManager()
        priority_value, _ = self._normalise_priority(default_priority, DEFAULT_PRIORITY)
        self._default_priority = priority_value
        self._log_delivery_activity = bool(log_delivery_activity)
        self._max_retries = max(0, int(max_retries))
        self._retry_delays = retry_delays or DEFAULT_RETRY_DELAYS
        self._batch_size_per_account = max(1, int(batch_size_per_account))

    # --------------------------------------------------------------------- utils
    @staticmethod
    def _utc_now_iso() -> str:
        """Return the current UTC timestamp as ISO-8601 string."""
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _utc_now_epoch() -> int:
        """Return the current UTC timestamp as seconds since epoch."""
        return int(datetime.now(timezone.utc).timestamp())

    async def init(self) -> None:
        """Initialise persistence and reload scheduler rules from storage."""
        await self.persistence.init_db()
        self._rules = await self.persistence.list_rules()
        if not self._rules:
            self._active = False
        await self._refresh_queue_gauge()

    def _normalise_priority(self, value: Any, default: Any = DEFAULT_PRIORITY) -> Tuple[int, str]:
        """Coerce user supplied priority into the internal representation."""
        if isinstance(default, str):
            fallback = LABEL_TO_PRIORITY.get(default.lower(), DEFAULT_PRIORITY)
        elif isinstance(default, (int, float)):
            try:
                fallback = int(default)
            except (TypeError, ValueError):
                fallback = DEFAULT_PRIORITY
        else:
            fallback = DEFAULT_PRIORITY
        fallback = max(0, min(fallback, max(PRIORITY_LABELS)))

        if value is None:
            priority = fallback
        elif isinstance(value, str):
            key = value.lower()
            if key in LABEL_TO_PRIORITY:
                priority = LABEL_TO_PRIORITY[key]
            else:
                try:
                    priority = int(value)
                except ValueError:
                    priority = fallback
        else:
            try:
                priority = int(value)
            except (TypeError, ValueError):
                priority = fallback
        priority = max(0, min(priority, max(PRIORITY_LABELS)))
        label = PRIORITY_LABELS.get(priority, PRIORITY_LABELS[fallback])
        return priority, label

    @staticmethod
    def _summarise_addresses(value: Any) -> str:
        """Return a compact textual representation of recipient-like values."""
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

    # ---------------------------------------------------------------- scheduling
    def _has_enabled_rules(self) -> bool:
        """Check whether at least one scheduling rule is currently enabled."""
        return any(rule.get("enabled", True) for rule in self._rules)

    def _is_scheduler_ready(self) -> bool:
        """Return ``True`` when the scheduler loop is allowed to notify clients."""
        return self._active and self._has_enabled_rules()

    def _current_interval_from_schedule(self) -> int:
        """Compute the polling interval (in seconds) based on the rule set."""
        if not self._has_enabled_rules():
            return 60
        now = datetime.now(self.timezone)
        weekday = now.weekday()
        hour = now.hour
        interval = 60
        for rule in self._rules:
            if not rule.get("enabled", True):
                continue
            days = rule.get("days", [])
            if days and weekday not in days:
                continue
            start = rule.get("start_hour")
            end = rule.get("end_hour")
            cross = rule.get("cross_midnight", False)
            in_window = False
            if start is None or end is None:
                in_window = True
            elif cross:
                if hour >= start or hour < end:
                    in_window = True
            else:
                if start <= hour < end:
                    in_window = True
            if in_window:
                interval = int(rule.get("interval_minutes", interval // 60 or 1)) * 60
        return max(1, interval)

    # ------------------------------------------------------------------ commands
    async def handle_command(self, cmd: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Execute one of the external control commands."""
        payload = payload or {}
        if cmd == "run now":
            self._wake_event.set()
            return {"ok": True}
        if cmd == "suspend":
            self._active = False
            return {"ok": True, "active": False}
        if cmd == "activate":
            self._active = True
            return {"ok": True, "active": True}
        if cmd == "schedule":
            rules = payload.get("rules", []) if isinstance(payload, dict) else []
            await self.persistence.clear_rules()
            self._rules = []
            for idx, rule in enumerate(rules):
                await self._add_rule(rule, priority=idx)
            self._active = bool(payload.get("active", self._active)) and self._has_enabled_rules()
            return {"ok": True, "rules": self._rules}
        if cmd == "addAccount":
            await self.persistence.add_account(payload)
            return {"ok": True}
        if cmd == "listAccounts":
            accounts = await self.persistence.list_accounts()
            return {"ok": True, "accounts": accounts}
        if cmd == "deleteAccount":
            account_id = payload.get("id")
            await self.persistence.delete_account(account_id)
            await self._refresh_queue_gauge()
            return {"ok": True}
        if cmd == "deleteMessages":
            ids = payload.get("ids") if isinstance(payload, dict) else []
            removed, not_found = await self._delete_messages(ids or [])
            await self._refresh_queue_gauge()
            return {"ok": True, "removed": removed, "not_found": not_found}
        if cmd == "listMessages":
            active_only = bool(payload.get("active_only", False)) if isinstance(payload, dict) else False
            messages = await self.persistence.list_messages(active_only=active_only)
            return {"ok": True, "messages": messages}
        if cmd == "addMessages":
            return await self._handle_add_messages(payload)
        if cmd == "addRule":
            await self._add_rule(payload or {})
            return {"ok": True, "rules": self._rules}
        if cmd == "deleteRule":
            rule_id = payload.get("id") if isinstance(payload, dict) else None
            if rule_id is None:
                return {"ok": False, "error": "missing 'id'"}
            await self.persistence.delete_rule(int(rule_id))
            self._rules = await self.persistence.list_rules()
            if not self._has_enabled_rules():
                self._active = False
            return {"ok": True, "rules": self._rules}
        if cmd == "listRules":
            return {"ok": True, "rules": self._rules}
        if cmd == "setRuleEnabled":
            rule_id = payload.get("id") if isinstance(payload, dict) else None
            enabled = payload.get("enabled") if isinstance(payload, dict) else None
            if rule_id is None or enabled is None:
                return {"ok": False, "error": "missing 'id' or 'enabled'"}
            await self.persistence.set_rule_enabled(int(rule_id), bool(enabled))
            self._rules = await self.persistence.list_rules()
            if not self._has_enabled_rules():
                self._active = False
            return {"ok": True, "rules": self._rules}
        return {"ok": False, "error": "unknown command"}

    async def _handle_add_messages(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        messages = payload.get("messages") if isinstance(payload, dict) else None
        if not isinstance(messages, list):
            return {"ok": False, "error": "messages must be a list"}
        if len(messages) > self._max_enqueue_batch:
            return {"ok": False, "error": f"Cannot enqueue more than {self._max_enqueue_batch} messages at once"}

        default_priority_value = 2
        if "default_priority" in payload:
            default_priority_value, _ = self._normalise_priority(payload.get("default_priority"), 2)

        validated: List[Dict[str, Any]] = []
        rejected: List[Dict[str, Any]] = []
        for item in messages:
            if not isinstance(item, dict):
                rejected.append({"id": None, "reason": "invalid payload"})
                continue
            is_valid, reason = await self._validate_enqueue_payload(item)
            if not is_valid:
                rejected.append({"id": item.get("id"), "reason": reason})
                continue
            priority, _ = self._normalise_priority(item.get("priority"), default_priority_value)
            item["priority"] = priority
            if "deferred_ts" in item and item["deferred_ts"] is None:
                item.pop("deferred_ts")
            validated.append(item)

        if not validated:
            return {"ok": False, "error": "all messages rejected", "rejected": rejected}

        existing_ids = await self.persistence.existing_message_ids(msg.get("id") for msg in validated)
        for msg in list(validated):
            if msg["id"] in existing_ids:
                rejected.append({"id": msg["id"], "reason": "duplicate id"})
                validated.remove(msg)

        entries = [
            {
                "id": msg["id"],
                "account_id": msg.get("account_id"),
                "priority": int(msg["priority"]),
                "payload": msg,
                "deferred_ts": msg.get("deferred_ts"),
            }
            for msg in validated
        ]
        inserted = await self.persistence.insert_messages(entries)
        for msg in validated:
            if msg["id"] not in inserted:
                rejected.append({"id": msg["id"], "reason": "duplicate id"})

        await self._refresh_queue_gauge()

        result: Dict[str, Any] = {
            "ok": True,
            "queued": len([mid for mid in inserted if mid]),
            "rejected": rejected,
        }
        return result

    async def _delete_messages(self, message_ids: Iterable[str]) -> Tuple[int, List[str]]:
        ids = {mid for mid in message_ids if mid}
        if not ids:
            return 0, []
        removed = 0
        missing: List[str] = []
        for mid in sorted(ids):
            if await self.persistence.delete_message(mid):
                removed += 1
            else:
                missing.append(mid)
        return removed, missing

    async def _add_rule(self, rule: Dict[str, Any], priority: Optional[int] = None) -> None:
        """Normalise and store a rule, assigning a deterministic priority."""
        rules = await self.persistence.list_rules()
        next_priority = priority if priority is not None else (rules[-1]["priority"] + 1 if rules else 0)
        rule_copy = rule.copy()
        rule_copy.setdefault("interval_minutes", 1)
        rule_copy["priority"] = next_priority
        rule_copy["days"] = [int(d) for d in rule_copy.get("days", [])]
        rule_copy["enabled"] = bool(rule_copy.get("enabled", True))
        rule_copy["cross_midnight"] = bool(rule_copy.get("cross_midnight", False))
        await self.persistence.add_rule(rule_copy)
        self._rules = await self.persistence.list_rules()
        if self._has_enabled_rules() and not self._active:
            self._active = True

    # ----------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        """Start the background scheduler and maintenance tasks."""
        self.logger.debug("Starting AsyncMailCore...")
        await self.init()
        self._stop.clear()
        self.logger.debug("Creating SMTP dispatch loop task...")
        self._task_smtp = asyncio.create_task(self._smtp_dispatch_loop(), name="smtp-dispatch-loop")
        self.logger.debug("Creating client report loop task...")
        self._task_client = asyncio.create_task(self._client_report_loop(), name="client-report-loop")
        if not self._test_mode:
            self.logger.debug("Creating cleanup loop task...")
            self._task_cleanup = asyncio.create_task(self._cleanup_loop(), name="smtp-cleanup-loop")
        self.logger.debug("All background tasks created")

    async def stop(self) -> None:
        """Stop the background tasks gracefully."""
        self._stop.set()
        self._wake_event.set()
        self._wake_client_event.set()
        await asyncio.gather(
            *(task for task in [self._task_smtp, self._task_client, self._task_cleanup] if task),
            return_exceptions=True,
        )

    # --------------------------------------------------------------- SMTP logic
    async def _smtp_dispatch_loop(self) -> None:
        """Continuously pick messages from storage and attempt delivery."""
        self.logger.debug("SMTP dispatch loop started")
        first_iteration = True
        while not self._stop.is_set():
            if first_iteration and self._test_mode:
                self.logger.info("First iteration in test mode, waiting for wakeup")
                await self._wait_for_wakeup(self._send_loop_interval)
            first_iteration = False
            try:
                self.logger.debug("Processing SMTP cycle...")
                processed = await self._process_smtp_cycle()
                self.logger.debug(f"SMTP cycle processed={processed}")
                # If messages were sent, trigger immediate client report sync
                if processed:
                    self.logger.debug("Messages sent, triggering immediate client report sync")
                    self._wake_client_event.set()
            except Exception as exc:  # pragma: no cover - defensive
                self.logger.exception("Unhandled error in SMTP dispatch loop: %s", exc)
                processed = False
            if not processed:
                self.logger.debug(f"No messages processed, waiting {self._send_loop_interval}s")
                await self._wait_for_wakeup(self._send_loop_interval)

    async def _process_smtp_cycle(self) -> bool:
        """Process one batch of messages ready for delivery, respecting per-account batch limits."""
        now_ts = self._utc_now_epoch()
        self.logger.debug(f"Fetching ready messages (now_ts={now_ts}, limit={self._smtp_batch_size})")
        batch = await self.persistence.fetch_ready_messages(limit=self._smtp_batch_size, now_ts=now_ts)
        self.logger.debug(f"Fetched {len(batch)} ready messages")
        if not batch:
            await self._refresh_queue_gauge()
            return False

        # Group messages by account_id and apply per-account batch limit
        from collections import defaultdict
        messages_by_account = defaultdict(list)
        for entry in batch:
            account_id = entry.get("message", {}).get("account_id") or "default"
            messages_by_account[account_id].append(entry)

        # Process messages respecting per-account batch size
        processed_any = False
        for account_id, account_messages in messages_by_account.items():
            # Get account-specific batch_size if available, otherwise use global default
            account_batch_size = self._batch_size_per_account
            if account_id and account_id != "default":
                try:
                    account_data = await self.persistence.get_account(account_id)
                    if account_data and account_data.get("batch_size"):
                        account_batch_size = int(account_data["batch_size"])
                except Exception:
                    pass  # Fall back to global default on any error

            # Limit messages for this account to its batch_size
            messages_to_send = account_messages[:account_batch_size]
            skipped_count = len(account_messages) - len(messages_to_send)

            if skipped_count > 0:
                self.logger.info(
                    f"Account {account_id}: processing {len(messages_to_send)} messages, "
                    f"deferring {skipped_count} messages to next cycle (batch_size={account_batch_size})"
                )

            for entry in messages_to_send:
                self.logger.debug(f"Dispatching message {entry.get('id')} for account {account_id}")
                await self._dispatch_message(entry, now_ts)
                processed_any = True

        await self._refresh_queue_gauge()
        return processed_any

    async def _dispatch_message(self, entry: Dict[str, Any], now_ts: int) -> None:
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
        if msg_id:
            await self.persistence.clear_deferred(msg_id)
        try:
            email_msg, envelope_from = await self._build_email(message)
        except KeyError as exc:
            reason = f"missing {exc}"
            await self.persistence.mark_error(msg_id, now_ts, reason)
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

        event = await self._send_with_limits(email_msg, envelope_from, msg_id, message)
        if event:
            await self._publish_result(event)

    async def _send_with_limits(
        self,
        msg: EmailMessage,
        envelope_from: Optional[str],
        msg_id: Optional[str],
        payload: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Send a message enforcing rate limits and bookkeeping."""
        account_id = payload.get("account_id")
        try:
            host, port, user, password, acc = await self._resolve_account(account_id)
        except AccountConfigurationError as exc:
            error_ts = self._utc_now_epoch()
            await self.persistence.mark_error(msg_id or "", error_ts, str(exc))
            return {
                "id": msg_id,
                "status": "error",
                "error": str(exc),
                "error_code": exc.code,
                "timestamp": self._utc_now_iso(),
                "account": account_id or "default",
            }

        use_tls = acc.get("use_tls")
        if use_tls is None:
            use_tls = int(port) == 465
        else:
            use_tls = bool(use_tls)
        resolved_account_id = account_id or acc.get("id") or "default"

        deferred_until = await self.rate_limiter.check_and_plan(acc)
        if deferred_until:
            await self.persistence.set_deferred(msg_id or "", deferred_until)
            self.metrics.inc_deferred(resolved_account_id)
            self.metrics.inc_rate_limited(resolved_account_id)
            return {
                "id": msg_id,
                "status": "deferred",
                "deferred_until": deferred_until,
                "timestamp": self._utc_now_iso(),
                "account": resolved_account_id,
            }

        try:
            smtp = await self.pool.get_connection(host, port, user, password, use_tls=use_tls)
            envelope_sender = envelope_from or msg.get("From")
            # Wrap send_message in timeout to prevent hanging (max 30s for large attachments)
            async with asyncio.timeout(30.0):
                await smtp.send_message(msg, sender=envelope_sender)
        except Exception as exc:
            # Classify the error as temporary or permanent
            is_temporary, smtp_code = _classify_smtp_error(exc)

            # Get current retry count from payload
            retry_count = payload.get("retry_count", 0)

            # Determine if we should retry
            should_retry = is_temporary and retry_count < self._max_retries

            if should_retry:
                # Calculate next retry timestamp
                delay = _calculate_retry_delay(retry_count, self._retry_delays)
                deferred_until = self._utc_now_epoch() + delay

                # Update payload with incremented retry count
                updated_payload = dict(payload)
                updated_payload["retry_count"] = retry_count + 1

                # Store updated payload and defer the message
                await self.persistence.update_message_payload(msg_id or "", updated_payload)
                await self.persistence.set_deferred(msg_id or "", deferred_until)
                self.metrics.inc_deferred(resolved_account_id)

                # Log the retry attempt
                error_info = f"{exc} (SMTP {smtp_code})" if smtp_code else str(exc)
                self.logger.warning(
                    "Temporary error for message %s (attempt %d/%d): %s - retrying in %ds",
                    msg_id,
                    retry_count + 1,
                    self._max_retries,
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
                # Permanent error or max retries exceeded - mark as failed
                error_ts = self._utc_now_epoch()
                error_info = f"{exc} (SMTP {smtp_code})" if smtp_code else str(exc)

                if retry_count >= self._max_retries:
                    error_info = f"Max retries ({self._max_retries}) exceeded: {error_info}"
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

                await self.persistence.mark_error(msg_id or "", error_ts, error_info)
                self.metrics.inc_error(resolved_account_id)

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
        await self.persistence.mark_sent(msg_id or "", sent_ts)
        await self.rate_limiter.log_send(resolved_account_id)
        self.metrics.inc_sent(resolved_account_id)
        return {
            "id": msg_id,
            "status": "sent",
            "timestamp": self._utc_now_iso(),
            "account": resolved_account_id,
        }

    # ----------------------------------------------------------- client reporting
    async def _client_report_loop(self) -> None:
        """
        Background coroutine that pushes delivery reports.

        Optimization: When SMTP loop sends messages, it triggers this loop immediately
        via _wake_client_event to reduce delivery report latency. Otherwise, this loop
        waits for the normal schedule interval.
        """
        first_iteration = True
        while not self._stop.is_set():
            if first_iteration and self._test_mode:
                await self._wait_for_client_wakeup(math.inf)
            first_iteration = False
            interval = math.inf if self._test_mode else self._current_interval_from_schedule()
            try:
                await self._process_client_cycle()
            except Exception as exc:  # pragma: no cover - defensive
                self.logger.exception("Unhandled error in client report loop: %s", exc)
            # Wait for wake event (triggered by SMTP loop) or interval timeout
            await self._wait_for_client_wakeup(interval)

    async def _process_client_cycle(self) -> None:
        """Perform one delivery report cycle."""
        if not self._is_scheduler_ready():
            return

        reports = await self.persistence.fetch_reports(self._smtp_batch_size)
        if not reports:
            # Still allow the client sync endpoint to trigger its own fetch if needed
            if self._client_sync_url and self._report_delivery_callable is None:
                try:
                    await self._send_delivery_reports([])
                except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                    self.logger.warning(
                        "Client sync endpoint %s not reachable: %s",
                        self._client_sync_url,
                        exc,
                    )
            await self._apply_retention()
            return

        payloads = [
            {
                "id": item.get("id"),
                "account_id": item.get("account_id"),
                "priority": item.get("priority"),
                "sent_ts": item.get("sent_ts"),
                "error_ts": item.get("error_ts"),
                "error": item.get("error"),
                "deferred_ts": item.get("deferred_ts"),
            }
            for item in reports
        ]
        try:
            await self._send_delivery_reports(payloads)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            target = self._client_sync_url or "custom callable"
            self.logger.warning("Client sync delivery failed (%s): %s", target, exc)
            return
        reported_ts = self._utc_now_epoch()
        await self.persistence.mark_reported((item["id"] for item in reports), reported_ts)
        await self._apply_retention()

    async def _apply_retention(self) -> None:
        """Delete reported messages older than the configured retention."""
        if self._report_retention_seconds <= 0:
            return
        threshold = self._utc_now_epoch() - self._report_retention_seconds
        removed = await self.persistence.remove_reported_before(threshold)
        if removed:
            await self._refresh_queue_gauge()

    # ---------------------------------------------------------------- housekeeping
    async def _cleanup_loop(self) -> None:
        """Background coroutine that keeps SMTP pooled connections healthy."""
        while not self._stop.is_set():
            await asyncio.sleep(150)
            await self.pool.cleanup()

    async def _refresh_queue_gauge(self) -> None:
        """Refresh the metric describing queued messages."""
        try:
            count = await self.persistence.count_active_messages()
        except Exception:  # pragma: no cover - defensive
            self.logger.exception("Failed to refresh queue gauge")
            return
        self.metrics.set_pending(count)

    async def _wait_for_wakeup(self, timeout: float | None) -> None:
        """Pause the loop while allowing external wake-ups via 'run now'."""
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
            async with asyncio.timeout(timeout):
                await self._wake_event.wait()
                self.logger.debug("Woken up by event")
        except asyncio.TimeoutError:
            self.logger.debug(f"Timeout after {timeout}s")
            return
        self._wake_event.clear()

    async def _wait_for_client_wakeup(self, timeout: float | None) -> None:
        """Pause the client report loop while allowing immediate wake-ups when messages are sent."""
        if self._stop.is_set():
            return
        if timeout is None:
            await self._wake_client_event.wait()
            self._wake_client_event.clear()
            return
        timeout = float(timeout)
        if math.isinf(timeout):
            await self._wake_client_event.wait()
            self._wake_client_event.clear()
            return
        timeout = max(0.0, timeout)
        if timeout == 0:
            await asyncio.sleep(0)
            return
        try:
            async with asyncio.timeout(timeout):
                await self._wake_client_event.wait()
        except asyncio.TimeoutError:
            return
        self._wake_client_event.clear()

    # ----------------------------------------------------------------- messaging
    async def results(self):
        """Yield delivery events to API consumers."""
        while True:
            event = await self._result_queue.get()
            yield event

    async def _put_with_backpressure(self, queue: asyncio.Queue[Any], item: Any, queue_name: str) -> None:
        """Push an item to a queue, avoiding unbounded growth by timing out."""
        try:
            await asyncio.wait_for(queue.put(item), timeout=self._queue_put_timeout)
        except asyncio.TimeoutError:  # pragma: no cover - defensive
            self.logger.error("Timed out while enqueuing item into %s queue; dropping item", queue_name)

    def _log_delivery_event(self, event: Dict[str, Any]) -> None:
        """Emit a console log describing the outcome of a delivery attempt."""
        if not self._log_delivery_activity:
            return
        status = (event.get("status") or "unknown").lower()
        msg_id = event.get("id") or "-"
        account = event.get("account") or event.get("account_id") or "default"
        if status == "sent":
            self.logger.info("Delivery succeeded for message %s (account=%s)", msg_id, account)
            return
        if status == "deferred":
            deferred_until = event.get("deferred_until")
            if isinstance(deferred_until, (int, float)):
                deferred_repr = (
                    datetime.fromtimestamp(float(deferred_until), timezone.utc)
                    .isoformat()
                    .replace("+00:00", "Z")
                )
            else:
                deferred_repr = deferred_until or "-"
            self.logger.info(
                "Delivery deferred for message %s (account=%s) until %s",
                msg_id,
                account,
                deferred_repr,
            )
            return
        if status == "error":
            reason = event.get("error") or event.get("error_code") or "unknown error"
            self.logger.warning(
                "Delivery failed for message %s (account=%s): %s",
                msg_id,
                account,
                reason,
            )
            return
        self.logger.info("Delivery event for message %s (account=%s): %s", msg_id, account, status)

    async def _publish_result(self, event: Dict[str, Any]) -> None:
        """Publish a delivery event while observing queue backpressure."""
        self._log_delivery_event(event)
        await self._put_with_backpressure(self._result_queue, event, "result")

    # ---------------------------------------------------------- SMTP primitives
    async def _resolve_account(self, account_id: Optional[str]) -> Tuple[str, int, Optional[str], Optional[str], Dict[str, Any]]:
        """Return SMTP credentials for the requested account or defaults."""
        if account_id:
            acc = await self.persistence.get_account(account_id)
            return acc["host"], int(acc["port"]), acc.get("user"), acc.get("password"), acc
        if self.default_host and self.default_port:
            return (
                self.default_host,
                int(self.default_port),
                self.default_user,
                self.default_password,
                {"id": "default", "use_tls": self.default_use_tls},
            )
        raise AccountConfigurationError()

    async def _build_email(self, data: Dict[str, Any]) -> Tuple[EmailMessage, str]:
        """Translate the command payload into an :class:`EmailMessage` and envelope sender."""

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
        subtype = "html" if data.get("content_type", "plain") == "html" else "plain"
        msg.set_content(data.get("body", ""), subtype=subtype)
        for header, value in (data.get("headers") or {}).items():
            if value is None:
                continue
            value_str = str(value)
            if header in msg:
                msg.replace_header(header, value_str)
            else:
                msg[header] = value_str

        attachments = data.get("attachments", []) or []
        if attachments:
            results = await asyncio.gather(
                *[self._fetch_attachment_with_timeout(att) for att in attachments],
                return_exceptions=True,
            )
            for att, result in zip(attachments, results):
                filename = att.get("filename", "file.bin")
                if isinstance(result, Exception):
                    self.logger.warning("Failed to fetch attachment %s: %s", filename, result)
                    continue
                if result is None:
                    self.logger.warning("Skipping attachment without data (filename=%s)", filename)
                    continue
                content, resolved_filename = result
                maintype, subtype = self.attachments.guess_mime(resolved_filename)
                msg.add_attachment(content, maintype=maintype, subtype=subtype, filename=resolved_filename)
        return msg, envelope_from

    async def _fetch_attachment_with_timeout(self, att: Dict[str, Any]) -> Optional[Tuple[bytes, str]]:
        """Fetch an attachment using the configured timeout budget."""
        try:
            content = await asyncio.wait_for(self.attachments.fetch(att), timeout=self._attachment_timeout)
        except asyncio.TimeoutError as exc:
            raise TimeoutError(f"Attachment {att.get('filename', 'file.bin')} fetch timed out") from exc
        if content is None:
            return None
        filename = att.get("filename", "file.bin")
        return content, filename

    # ------------------------------------------------------------ client bridge
    async def _send_delivery_reports(self, payloads: List[Dict[str, Any]]) -> None:
        """Send delivery report payloads to the configured proxy or callback."""
        if self._report_delivery_callable is not None:
            if self._log_delivery_activity:
                batch_size = len(payloads)
                ids_preview = ", ".join(
                    str(item.get("id")) for item in payloads[:5] if item.get("id")
                )
                if len(payloads) > 5:
                    ids_preview = f"{ids_preview}, ..." if ids_preview else "..."
                self.logger.info(
                    "Forwarding %d delivery report(s) via custom callable (ids=%s)",
                    batch_size,
                    ids_preview or "-",
                )
            for payload in payloads:
                await self._report_delivery_callable(payload)
            return
        if not self._client_sync_url:
            if payloads:
                raise RuntimeError("Client sync URL is not configured")
            return
        headers: Dict[str, str] = {}
        auth = None
        if self._client_sync_token:
            headers["Authorization"] = f"Bearer {self._client_sync_token}"
        elif self._client_sync_user:
            auth = aiohttp.BasicAuth(self._client_sync_user, self._client_sync_password or "")
        batch_size = len(payloads)
        if self._log_delivery_activity:
            ids_preview = ", ".join(str(item.get("id")) for item in payloads[:5] if item.get("id"))
            if len(payloads) > 5:
                ids_preview = f"{ids_preview}, ..." if ids_preview else "..."
            self.logger.info(
                "Posting delivery reports to client sync endpoint %s (count=%d, ids=%s)",
                self._client_sync_url,
                batch_size,
                ids_preview or "-",
            )
        else:
            self.logger.debug(
                "Posting delivery reports to client sync endpoint %s (count=%d)",
                self._client_sync_url,
                batch_size,
            )
        async with aiohttp.ClientSession() as session:
            async with session.post(
                self._client_sync_url,
                json={"delivery_report": payloads},
                auth=auth,
                headers=headers or None,
            ) as resp:
                resp.raise_for_status()
        if self._log_delivery_activity:
            self.logger.info("Client sync acknowledged delivery report batch (%d items)", batch_size)
        else:
            self.logger.debug("Delivery report batch delivered (%d items)", batch_size)

    # ------------------------------------------------------------- validations
    async def _validate_enqueue_payload(self, payload: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        msg_id = payload.get("id")
        if not msg_id:
            return False, "missing id"
        payload.setdefault("priority", 2)
        sender = payload.get("from")
        if not sender:
            return False, "missing from"
        recipients = payload.get("to")
        if not recipients:
            return False, "missing to"
        if isinstance(recipients, (list, tuple, set)):
            if not any(recipients):
                return False, "missing to"
        account_id = payload.get("account_id")
        if account_id:
            try:
                await self.persistence.get_account(account_id)
            except Exception:
                return False, "account not found"
        elif not (self.default_host and self.default_port):
            return False, "missing account configuration"
        return True, None
