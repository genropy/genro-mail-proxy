"""Core orchestration logic for the asynchronous mail dispatcher."""

import asyncio
from email.message import EmailMessage
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Tuple, List
from itertools import count

from .logger import get_logger
from .smtp_pool import SMTPPool
from .persistence import Persistence
from .rate_limit import RateLimiter
from .fetcher import Fetcher
from .attachments import AttachmentManager
from .prometheus import MailMetrics
from zoneinfo import ZoneInfo

class AsyncMailCore:
    """Coordinate scheduling, rate limiting, persistence and delivery."""
    def __init__(
        self,
        host: str = "localhost",
        port: int = 587,
        user: str | None = None,
        password: str | None = None,
        use_tls: bool | None = None,
        *,
        fetch_url: str | None = None,
        db_path: str | None = "/data/mail_service.db",
        logger=None,
        metrics: MailMetrics | None = None,
        start_active: bool = False,
        timezone: str = "Europe/Rome",
        result_queue_size: int = 1000,
        delivery_queue_size: int = 1000,
        message_queue_size: int = 10000,
        queue_put_timeout: float = 5.0,
        max_enqueue_batch: int = 1000,
        attachment_timeout: int = 30,
    ):
        """Prepare the runtime collaborators and scheduler state."""
        self.default_host = host
        self.default_port = port
        self.default_user = user
        self.default_password = password
        self.default_use_tls = bool(use_tls) if use_tls is not None else (int(port) == 465)

        self.logger = logger or get_logger()
        self.pool = SMTPPool()
        self.persistence = Persistence(db_path or ":memory:")
        self.fetcher = Fetcher(fetch_url=fetch_url)
        self.rate_limiter = RateLimiter(self.persistence)
        self.metrics = metrics or MailMetrics()
        self.timezone = ZoneInfo(timezone)
        self._queue_put_timeout = queue_put_timeout
        self._max_enqueue_batch = max_enqueue_batch
        self._attachment_timeout = attachment_timeout

        self._stop = asyncio.Event()
        self._active = start_active
        self._schedule: Dict[str, Any] | None = None
        self._result_queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue(maxsize=result_queue_size)
        self._message_queue: asyncio.PriorityQueue[Tuple[int, int, Dict[str, Any]]] = asyncio.PriorityQueue(maxsize=message_queue_size)
        self._queue_counter = count()
        self._queue_lock = asyncio.Lock()
        self._rules: List[Dict[str, Any]] = []
        self._delivery_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=delivery_queue_size)

        self.attachments = AttachmentManager()

    @staticmethod
    def _utc_now() -> str:
        """Return the current UTC timestamp encoded as an ISO-8601 string."""
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    async def init(self):
        """Initialise persistence and reload scheduler rules from storage."""
        await self.persistence.init_db()
        self._rules = await self.persistence.list_rules()
        if not self._rules:
            self._active = False

    # Scheduling
    def _has_enabled_rules(self) -> bool:
        """Check whether at least one scheduling rule is currently enabled."""
        return any(rule.get("enabled", True) for rule in self._rules)

    def _is_scheduler_ready(self) -> bool:
        """Return ``True`` when the scheduler loop is allowed to send mail."""
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

    # Commands
    async def handle_command(self, cmd: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Execute one of the external control commands.

        Parameters
        ----------
        cmd:
            Name of the command as received from the API layer.
        payload:
            Optional dictionary holding the command arguments.

        Returns
        -------
        dict
            ``{"ok": True}`` on success with additional fields depending on
            the command, or ``{"ok": False, "error": "..."}`` on validation
            errors.
        """
        payload = payload or {}
        if cmd == "run now":
            await self._flush_delivery_reports()
            await self._fetch_and_send_once(force=True)
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
            await self.persistence.delete_account(payload["id"])
            return {"ok": True}
        if cmd == "pendingMessages":
            pending = await self.persistence.list_pending()
            self.metrics.set_pending(len(pending))
            return {"ok": True, "pending": pending}
        if cmd == "listDeferred":
            return {"ok": True, "deferred": await self.persistence.list_deferred()}
        if cmd == "sendMessage":
            try:
                email_msg, envelope_from = await self._build_email(payload)
            except KeyError as e:
                return {"ok": False, "error": f"missing {e}"}

            msg_id = payload.get("id")
            account_id = payload.get("account_id")
            result = await self._send_with_limits(email_msg, envelope_from, msg_id, account_id)
            return {"ok": result.get("status") == "sent", "result": result}
        if cmd == "addMessages":
            messages = payload.get("messages") if isinstance(payload, dict) else None
            if not isinstance(messages, list):
                return {"ok": False, "error": "messages must be a list"}
            for item in messages:
                if not isinstance(item, dict):
                    return {"ok": False, "error": "each message must be an object"}
            await self._enqueue_messages(messages, default_priority=0)
            if self._is_scheduler_ready():
                await self._process_queue()
            return {"ok": True, "queued": len(messages)}
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

    # Build & send
    async def _resolve_account(self, account_id: Optional[str]) -> Tuple[str, int, Optional[str], Optional[str], Dict[str, Any]]:
        """Return SMTP credentials for the requested account or defaults."""
        if account_id:
            acc = await self.persistence.get_account(account_id)
            return acc["host"], int(acc["port"]), acc.get("user"), acc.get("password"), acc
        return self.default_host, self.default_port, self.default_user, self.default_password, {"id": "default", "use_tls": self.default_use_tls}

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
                mt, st = self.attachments.guess_mime(resolved_filename)
                msg.add_attachment(content, maintype=mt, subtype=st, filename=resolved_filename)
        return msg, envelope_from

    async def _enqueue_messages(self, messages: list[Dict[str, Any]], default_priority: int = 10):
        """Push messages into the internal priority queue."""
        if len(messages) > self._max_enqueue_batch:
            raise ValueError(f"Cannot enqueue more than {self._max_enqueue_batch} messages at once")
        for item in messages:
            priority_value = item.get("priority", default_priority)
            try:
                priority = int(priority_value)
            except (TypeError, ValueError):
                priority = default_priority
            await self._put_with_backpressure(
                self._message_queue,
                (priority, next(self._queue_counter), item),
                "message",
            )

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

    async def _process_queue(self):
        """Consume the message queue and trigger delivery for each entry."""
        async with self._queue_lock:
            while not self._message_queue.empty():
                _, _, data = await self._message_queue.get()
                await self._handle_message(data)
                self._message_queue.task_done()

    async def _handle_message(self, data: Dict[str, Any]):
        """Deliver or defer a single message pulled from the queue."""
        msg_id = data.get("id")
        account_id = data.get("account_id")
        try:
            email_msg, envelope_from = await self._build_email(data)
        except KeyError as e:
            event = {"id": msg_id, "status": "error", "error": f"missing {e}", "timestamp": self._utc_now()}
            await self._publish_result(event)
            await self._report_delivery(event)
            return
        if msg_id and account_id:
            deferred_until = await self.persistence.get_deferred_until(msg_id, account_id)
            if deferred_until and deferred_until > int(datetime.now(tz=timezone.utc).timestamp()):
                event = {
                    "id": msg_id,
                    "status": "deferred",
                    "deferred_until": deferred_until,
                    "timestamp": self._utc_now(),
                    "account": account_id,
                }
                await self._publish_result(event)
                await self._report_delivery(event)
                return
            else:
                await self.persistence.clear_deferred(msg_id)

        await self._send_with_limits(email_msg, envelope_from, msg_id, account_id)

    async def _fetch_and_send_once(self, *, force: bool = False):
        """Fetch new messages from the upstream service and process them."""
        if not force and not self._is_scheduler_ready():
            return
        messages = await self.fetcher.fetch_messages()
        if messages:
            await self._enqueue_messages(messages, default_priority=10)
        await self._process_queue()

    async def _flush_delivery_reports(self):
        """Send pending delivery reports back to the upstream service."""
        pending_reports = await self.persistence.list_delivery_reports()
        retries: List[str] = []

        # Drain notifications from the in-memory queue since we read from storage directly.
        while True:
            try:
                self._delivery_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            else:
                self._delivery_queue.task_done()

        for report in pending_reports:
            report_id = report["id"]
            payload = report["payload"]
            try:
                await self.fetcher.report_delivery(payload)
            except Exception as exc:
                self.logger.warning("Failed to report delivery: %s", exc)
                await self.persistence.increment_report_retry(report_id)
                retries.append(report_id)
            else:
                await self.persistence.delete_delivery_report(report_id)

        for report_id in retries:
            await self._put_with_backpressure(self._delivery_queue, report_id, "delivery")

    async def _send_with_limits(self, msg: EmailMessage, envelope_from: Optional[str], msg_id: Optional[str], account_id: Optional[str]):
        """Send a message enforcing rate limits and bookkeeping."""
        host, port, user, password, acc = await self._resolve_account(account_id)
        use_tls = acc.get("use_tls")
        if use_tls is None:
            use_tls = int(port) == 465
        else:
            use_tls = bool(use_tls)
        deferred_until = await self.rate_limiter.check_and_plan(acc)
        if deferred_until:
            await self.persistence.set_deferred(msg_id or "", acc["id"], deferred_until)
            self.metrics.inc_deferred(acc["id"] if account_id else "default")
            self.metrics.inc_rate_limited(acc["id"] if account_id else "default")
            event = {
                "id": msg_id,
                "status": "deferred",
                "deferred_until": deferred_until,
                "timestamp": self._utc_now(),
                "account": acc["id"],
            }
            await self._publish_result(event)
            await self._report_delivery(event)
            return event
        if msg_id:
            await self.persistence.add_pending(msg_id, msg.get("To"), msg.get("Subject", ""))
        try:
            smtp = await self.pool.get_connection(host, port, user, password, use_tls=use_tls)
            envelope_sender = envelope_from or msg.get("From")
            await smtp.send_message(msg, from_addr=envelope_sender)
            await self.persistence.remove_pending(msg_id or "")
            await self.rate_limiter.log_send(acc["id"] if account_id else "default")
            self.metrics.inc_sent(acc["id"] if account_id else "default")
            event = {
                "id": msg_id,
                "status": "sent",
                "timestamp": self._utc_now(),
                "account": acc["id"] if account_id else "default",
            }
            await self._publish_result(event)
            await self._report_delivery(event)
            return event
        except Exception as e:
            await self.persistence.remove_pending(msg_id or "")
            self.metrics.inc_error(acc["id"] if account_id else "default")
            event = {
                "id": msg_id,
                "status": "error",
                "error": str(e),
                "timestamp": self._utc_now(),
                "account": acc["id"] if account_id else "default",
            }
            await self._publish_result(event)
            await self._report_delivery(event)
            return event

    async def _report_delivery(self, event: Dict[str, Any]):
        """Persist and queue a delivery event to be sent to the upstream service."""
        report_id = await self.persistence.save_delivery_report(event)
        await self._put_with_backpressure(self._delivery_queue, report_id, "delivery")

    async def start(self):
        """Start the background scheduler and maintenance tasks."""
        await self.init()
        self._stop.clear()
        self._task_fetch = asyncio.create_task(self._fetch_loop())
        self._task_cleanup = asyncio.create_task(self._cleanup_loop())

    async def stop(self):
        """Stop the background tasks gracefully."""
        self._stop.set()
        await asyncio.gather(self._task_fetch, self._task_cleanup, return_exceptions=True)

    async def _fetch_loop(self):
        """Background coroutine that periodically polls for new messages."""
        while not self._stop.is_set():
            if not self._is_scheduler_ready():
                await asyncio.sleep(5)
                continue
            await self._flush_delivery_reports()
            await self._fetch_and_send_once()
            await asyncio.sleep(self._current_interval_from_schedule())

    async def _cleanup_loop(self):
        """Background coroutine that keeps SMTP pooled connections healthy."""
        while not self._stop.is_set():
            await asyncio.sleep(150)
            await self.pool.cleanup()

    async def results(self):
        """Yield delivery events to API consumers."""
        while True:
            r = await self._result_queue.get()
            yield r

    async def _put_with_backpressure(self, queue: asyncio.Queue[Any], item: Any, queue_name: str) -> None:
        """Push an item to a queue, avoiding unbounded growth by timing out."""
        try:
            await asyncio.wait_for(queue.put(item), timeout=self._queue_put_timeout)
        except asyncio.TimeoutError:
            self.logger.error("Timed out while enqueuing item into %s queue; dropping item", queue_name)

    async def _publish_result(self, event: Dict[str, Any]) -> None:
        """Publish a delivery event while observing queue backpressure."""
        await self._put_with_backpressure(self._result_queue, event, "result")

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
