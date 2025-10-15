"""Core orchestration logic for the asynchronous mail dispatcher."""

import asyncio
from email.message import EmailMessage
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Tuple, List, Awaitable, Callable, Iterable, Set
from itertools import count

import aiohttp

from .logger import get_logger
from .smtp_pool import SMTPPool
from .persistence import Persistence
from .rate_limit import RateLimiter
from .attachments import AttachmentManager
from .prometheus import MailMetrics
from zoneinfo import ZoneInfo

PRIORITY_LABELS = {
    0: "immediate",
    1: "high",
    2: "medium",
    3: "low",
}
LABEL_TO_PRIORITY = {label: value for value, label in PRIORITY_LABELS.items()}
DEFAULT_PRIORITY = 1

class AccountConfigurationError(RuntimeError):
    """Raised when a message is missing the information required to resolve an SMTP account."""

    def __init__(self, message: str = "Missing SMTP account configuration"):
        super().__init__(message)
        self.code = "missing_account_configuration"


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
        delivery_queue_size: int = 1000,
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
        send_loop_interval: float = 0.2,
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
        self._send_loop_interval = max(0.05, float(send_loop_interval))

        self._stop = asyncio.Event()
        self._active = start_active
        self._schedule: Dict[str, Any] | None = None
        self._result_queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue(maxsize=result_queue_size)
        self._message_queue: asyncio.PriorityQueue[Tuple[int, int, Dict[str, Any]]] = asyncio.PriorityQueue(maxsize=message_queue_size)
        self._queue_counter = count()
        self._queue_lock = asyncio.Lock()
        self._rules: List[Dict[str, Any]] = []
        self._delivery_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=delivery_queue_size)
        self._task_sender: Optional[asyncio.Task] = None
        self._task_delivery: Optional[asyncio.Task] = None
        self._task_cleanup: Optional[asyncio.Task] = None

        self._client_sync_url = client_sync_url
        self._client_sync_user = client_sync_user
        self._client_sync_password = client_sync_password
        self._client_sync_token = client_sync_token
        self._report_delivery_callable = report_delivery_callable

        self.attachments = AttachmentManager()
        priority_value, _ = self._normalise_priority(default_priority, DEFAULT_PRIORITY)
        self._default_priority = priority_value

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
            await self._process_queue()
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
            await self._purge_account_messages(account_id)
            pending = await self.persistence.list_pending()
            self.metrics.set_pending(len(pending))
            return {"ok": True}
        if cmd == "deleteMessages":
            message_ids = []
            if isinstance(payload, dict):
                message_ids = payload.get("ids") or []
            removed, missing = await self._delete_messages(message_ids)
            pending = await self.persistence.list_pending()
            self.metrics.set_pending(len(pending))
            return {"ok": True, "removed": removed, "not_found": missing}
        if cmd == "pendingMessages":
            pending = await self.persistence.list_pending()
            self.metrics.set_pending(len(pending))
            return {"ok": True, "pending": pending}
        if cmd == "listDeferred":
            return {"ok": True, "deferred": await self.persistence.list_deferred()}
        if cmd == "listMessages":
            active_only = True
            if isinstance(payload, dict) and "active_only" in payload:
                active_only = bool(payload.get("active_only"))
            messages = await self.persistence.list_messages(active_only=active_only)
            return {"ok": True, "messages": messages}
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
            validated: List[Dict[str, Any]] = []
            rejected: List[Dict[str, Any]] = []
            for item in messages:
                if not isinstance(item, dict):
                    rejected.append({"id": None, "reason": "invalid payload"})
                    continue
                is_valid, reason = await self._validate_enqueue_payload(item)
                if is_valid:
                    validated.append(item)
                else:
                    rejected.append({"id": item.get("id"), "reason": reason})
            if not validated:
                return {"ok": False, "status": "error", "error": "all messages rejected", "rejected": rejected, "messages": []}
            default_priority_value = self._default_priority
            if isinstance(payload, dict) and "default_priority" in payload:
                default_priority_value, _ = self._normalise_priority(payload.get("default_priority"), self._default_priority)
            has_immediate, tracked_messages = await self._enqueue_messages(validated, default_priority=default_priority_value)
            response_items: List[Dict[str, Any]] = list(tracked_messages)
            if rejected:
                error_ts = self._utc_now()
                for entry in rejected:
                    response_items.append(
                        {
                            "id": entry.get("id"),
                            "account_id": entry.get("account_id"),
                            "priority": None,
                            "priority_label": None,
                            "status": "error",
                            "proxy_ts": None,
                            "error_ts": error_ts,
                            "error_msg": entry.get("reason"),
                        }
                    )
            result: Dict[str, Any] = {"ok": True, "status": "ok", "queued": len(validated), "messages": response_items}
            if rejected:
                result["rejected"] = rejected
            if has_immediate:
                await self.handle_command("run now", {})
            return result
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
                mt, st = self.attachments.guess_mime(resolved_filename)
                msg.add_attachment(content, maintype=mt, subtype=st, filename=resolved_filename)
        return msg, envelope_from

    async def _enqueue_messages(self, messages: list[Dict[str, Any]], default_priority: int | str | None = None) -> Tuple[bool, List[Dict[str, Any]]]:
        """Push messages into the internal priority queue."""
        if len(messages) > self._max_enqueue_batch:
            raise ValueError(f"Cannot enqueue more than {self._max_enqueue_batch} messages at once")
        has_immediate = False
        summaries: List[Dict[str, Any]] = []
        resolved_default, _ = self._normalise_priority(default_priority, self._default_priority)
        for item in messages:
            priority, label = self._normalise_priority(item.get("priority"), resolved_default)
            item["priority"] = priority
            if priority == 0:
                has_immediate = True
            queued_ts = self._utc_now()
            await self.persistence.save_message(item.get("id"), item, priority, label)
            await self._put_with_backpressure(
                self._message_queue,
                (priority, next(self._queue_counter), item),
                "message",
            )
            summaries.append(
                {
                    "id": item.get("id"),
                    "account_id": item.get("account_id"),
                    "priority": priority,
                    "priority_label": label,
                    "status": "queued",
                    "proxy_ts": queued_ts,
                    "error_ts": None,
                    "error_msg": None,
                }
            )
        return has_immediate, summaries

    async def _purge_account_messages(self, account_id: Optional[str]) -> None:
        """Remove any queued messages for the given account."""
        if not account_id:
            return
        await self._remove_from_memory_queue(account_id=account_id)

    async def _remove_from_memory_queue(self, ids: Optional[Set[str]] = None, account_id: Optional[str] = None) -> Set[str]:
        """Remove queued messages matching the given identifiers or account."""
        ids = set(ids or set())
        removed: Set[str] = set()
        if not ids and not account_id:
            return removed
        async with self._queue_lock:
            retained: List[Tuple[int, int, Dict[str, Any]]] = []
            while not self._message_queue.empty():
                priority, counter, item = await self._message_queue.get()
                try:
                    message_id = item.get("id")
                    message_account = item.get("account_id")
                    remove = False
                    if ids and message_id in ids:
                        remove = True
                    if account_id and message_account == account_id:
                        remove = True
                    if remove:
                        if message_id:
                            removed.add(message_id)
                        continue
                    retained.append((priority, counter, item))
                finally:
                    self._message_queue.task_done()
            for entry in retained:
                await self._message_queue.put(entry)
        return removed

    async def _delete_messages(self, message_ids: Iterable[str]) -> Tuple[int, List[str]]:
        """Delete messages from queues, pending lists and deferred storage."""
        ids = {mid for mid in (message_ids or []) if mid}
        if not ids:
            return 0, []
        removed_from_queue = await self._remove_from_memory_queue(ids=ids)
        removed_count = 0
        missing: List[str] = []
        for mid in ids:
            touched = mid in removed_from_queue
            if await self.persistence.delete_message(mid):
                touched = True
            if await self.persistence.remove_pending(mid):
                touched = True
            if await self.persistence.clear_deferred(mid):
                touched = True
            if touched:
                removed_count += 1
            else:
                missing.append(mid)
        missing.sort()
        return removed_count, missing

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

    async def _process_queue(self) -> bool:
        """Consume the message queue and trigger delivery for each entry."""
        processed = False
        async with self._queue_lock:
            while not self._message_queue.empty():
                _, _, data = await self._message_queue.get()
                try:
                    msg_id = data.get("id")
                    if msg_id:
                        await self.persistence.update_message_status(msg_id, "pending")
                    await self._handle_message(data)
                finally:
                    self._message_queue.task_done()
                processed = True
        return processed

    async def _handle_message(self, data: Dict[str, Any]):
        """Deliver or defer a single message pulled from the queue."""
        msg_id = data.get("id")
        account_id = data.get("account_id")
        try:
            email_msg, envelope_from = await self._build_email(data)
        except KeyError as e:
            event = {"id": msg_id, "status": "error", "error": f"missing {e}", "timestamp": self._utc_now()}
            if msg_id:
                await self.persistence.update_message_status(msg_id, "error")
            await self._publish_result(event)
            await self._queue_delivery_event(event)
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
                await self.persistence.update_message_status(msg_id, "deferred")
                await self._publish_result(event)
                await self._queue_delivery_event(event)
                return
            else:
                await self.persistence.clear_deferred(msg_id)

        await self._send_with_limits(email_msg, envelope_from, msg_id, account_id)

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

        if not pending_reports:
            # No delivery reports to send, but still contact the proxy to trigger message polling.
            if self._report_delivery_callable is None and self._client_sync_url:
                try:
                    await self._send_delivery_reports([])
                except Exception as exc:
                    self.logger.warning("Failed to contact client sync endpoint: %s", exc)
            return

        payloads = [report["payload"] for report in pending_reports]
        try:
            await self._send_delivery_reports(payloads)
        except Exception as exc:
            self.logger.warning("Failed to report delivery: %s", exc)
            for report in pending_reports:
                report_id = report["id"]
                await self.persistence.increment_report_retry(report_id)
                retries.append(report_id)
        else:
            for report in pending_reports:
                await self.persistence.delete_delivery_report(report["id"])

        for report_id in retries:
            await self._put_with_backpressure(self._delivery_queue, report_id, "delivery")

    async def _send_with_limits(self, msg: EmailMessage, envelope_from: Optional[str], msg_id: Optional[str], account_id: Optional[str]):
        """Send a message enforcing rate limits and bookkeeping."""
        try:
            host, port, user, password, acc = await self._resolve_account(account_id)
        except AccountConfigurationError as exc:
            event = {
                "id": msg_id,
                "status": "error",
                "error": str(exc),
                "error_code": exc.code,
                "timestamp": self._utc_now(),
                "account": account_id or "default",
            }
            if msg_id:
                await self.persistence.update_message_status(msg_id, "error")
            await self._publish_result(event)
            await self._queue_delivery_event(event)
            return event
        use_tls = acc.get("use_tls")
        if use_tls is None:
            use_tls = int(port) == 465
        else:
            use_tls = bool(use_tls)
        resolved_account_id = account_id or acc.get("id") or "default"
        deferred_until = await self.rate_limiter.check_and_plan(acc)
        if deferred_until:
            await self.persistence.set_deferred(msg_id or "", resolved_account_id, deferred_until)
            self.metrics.inc_deferred(resolved_account_id)
            self.metrics.inc_rate_limited(resolved_account_id)
            event = {
                "id": msg_id,
                "status": "deferred",
                "deferred_until": deferred_until,
                "timestamp": self._utc_now(),
                "account": resolved_account_id,
            }
            if msg_id:
                await self.persistence.update_message_status(msg_id, "deferred")
            await self._publish_result(event)
            await self._queue_delivery_event(event)
            return event
        if msg_id:
            await self.persistence.add_pending(msg_id, msg.get("To"), msg.get("Subject", ""), resolved_account_id)
        try:
            smtp = await self.pool.get_connection(host, port, user, password, use_tls=use_tls)
            envelope_sender = envelope_from or msg.get("From")
            await smtp.send_message(msg, from_addr=envelope_sender)
            await self.persistence.remove_pending(msg_id or "")
            await self.rate_limiter.log_send(resolved_account_id)
            self.metrics.inc_sent(resolved_account_id)
            event = {
                "id": msg_id,
                "status": "sent",
                "timestamp": self._utc_now(),
                "account": resolved_account_id,
            }
            if msg_id:
                await self.persistence.delete_message(msg_id)
            await self._publish_result(event)
            await self._queue_delivery_event(event)
            return event
        except Exception as e:
            await self.persistence.remove_pending(msg_id or "")
            self.metrics.inc_error(resolved_account_id)
            event = {
                "id": msg_id,
                "status": "error",
                "error": str(e),
                "timestamp": self._utc_now(),
                "account": resolved_account_id,
            }
            if msg_id:
                await self.persistence.update_message_status(msg_id, "error")
            await self._publish_result(event)
            await self._queue_delivery_event(event)
            return event

    async def _queue_delivery_event(self, event: Dict[str, Any]):
        """Persist and queue a delivery event to be sent to the upstream service."""
        report_id = await self.persistence.save_delivery_report(event)
        await self._put_with_backpressure(self._delivery_queue, report_id, "delivery")

    async def start(self):
        """Start the background scheduler and maintenance tasks."""
        await self.init()
        self._stop.clear()
        self._task_sender = asyncio.create_task(self._send_loop())
        self._task_delivery = asyncio.create_task(self._delivery_loop())
        self._task_cleanup = asyncio.create_task(self._cleanup_loop())

    async def stop(self):
        """Stop the background tasks gracefully."""
        self._stop.set()
        await asyncio.gather(
            *(task for task in [self._task_sender, self._task_delivery, self._task_cleanup] if task),
            return_exceptions=True,
        )

    async def _delivery_loop(self):
        """Background coroutine that periodically pushes delivery reports."""
        while not self._stop.is_set():
            if not self._is_scheduler_ready():
                await asyncio.sleep(5)
                continue
            await self._flush_delivery_reports()
            await asyncio.sleep(self._current_interval_from_schedule())

    async def _send_loop(self):
        """Background coroutine that continuously drains the message queue."""
        while not self._stop.is_set():
            processed = await self._process_queue()
            if not processed:
                await asyncio.sleep(self._send_loop_interval)

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

    async def _send_delivery_reports(self, payloads: List[Dict[str, Any]]) -> None:
        if self._report_delivery_callable is not None:
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
        self.logger.info(
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

    async def _validate_enqueue_payload(self, payload: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        msg_id = payload.get("id")
        if not msg_id:
            return False, "missing id"
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
        elif not (self.default_host and self.default_port is not None):
            return False, "missing account configuration"
        return True, None

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
