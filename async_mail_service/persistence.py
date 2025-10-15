"""SQLite backed persistence used by the mail dispatcher."""

import json
import uuid

import aiosqlite
from typing import List, Dict, Any, Optional

class Persistence:
    """Helper class responsible for reading and writing service state."""

    def __init__(self, db_path: str = "/data/mail_service.db"):
        """Persist data to the given database path (``:memory:`` allowed)."""
        self.db_path = db_path or ":memory:"

    async def init_db(self) -> None:
        """Create the database schema if it is not already present."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS accounts (
                    id TEXT PRIMARY KEY,
                    host TEXT NOT NULL,
                    port INTEGER NOT NULL,
                    user TEXT,
                    password TEXT,
                    ttl INTEGER DEFAULT 300,
                    limit_per_minute INTEGER,
                    limit_per_hour INTEGER,
                    limit_per_day INTEGER,
                    limit_behavior TEXT,
                    use_tls INTEGER,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            try:
                await db.execute("ALTER TABLE accounts ADD COLUMN use_tls INTEGER")
            except aiosqlite.OperationalError:
                pass
            await db.execute("""
                CREATE TABLE IF NOT EXISTS schedule_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT,
                    enabled INTEGER DEFAULT 1,
                    priority INTEGER NOT NULL,
                    days TEXT,
                    start_hour INTEGER,
                    end_hour INTEGER,
                    cross_midnight INTEGER DEFAULT 0,
                    interval_minutes INTEGER NOT NULL
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS pending_messages (
                    id TEXT PRIMARY KEY,
                    to_addr TEXT,
                    subject TEXT,
                    account_id TEXT,
                    started_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            try:
                await db.execute("ALTER TABLE pending_messages ADD COLUMN account_id TEXT")
            except aiosqlite.OperationalError:
                pass
            await db.execute("""
                CREATE TABLE IF NOT EXISTS deferred_messages (
                    id TEXT PRIMARY KEY,
                    account_id TEXT,
                    deferred_until INTEGER,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS send_log (
                    account_id TEXT,
                    timestamp INTEGER
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS delivery_reports (
                    id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    retry_count INTEGER DEFAULT 0
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS queued_messages (
                    id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    priority_label TEXT,
                    account_id TEXT,
                    status TEXT NOT NULL DEFAULT 'queued',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            try:
                await db.execute("ALTER TABLE queued_messages ADD COLUMN priority_label TEXT")
            except aiosqlite.OperationalError:
                pass
            try:
                await db.execute("ALTER TABLE queued_messages ADD COLUMN account_id TEXT")
            except aiosqlite.OperationalError:
                pass
            try:
                await db.execute("ALTER TABLE queued_messages ADD COLUMN updated_at TEXT DEFAULT CURRENT_TIMESTAMP")
            except aiosqlite.OperationalError:
                pass
            await db.commit()

    # Accounts CRUD
    async def add_account(self, acc: Dict[str, Any]) -> None:
        """Insert or overwrite an SMTP account definition."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT OR REPLACE INTO accounts
                   (id, host, port, user, password, ttl, limit_per_minute, limit_per_hour, limit_per_day, limit_behavior, use_tls)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    acc["id"], acc["host"], int(acc["port"]),
                    acc.get("user"), acc.get("password"),
                    int(acc.get("ttl", 300)),
                    acc.get("limit_per_minute"),
                    acc.get("limit_per_hour"),
                    acc.get("limit_per_day"),
                    acc.get("limit_behavior", "defer"),
                    None if acc.get("use_tls") is None else (1 if acc.get("use_tls") else 0),
                )
            )
            await db.commit()

    async def list_accounts(self) -> List[Dict[str, Any]]:
        """Return all known SMTP accounts."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT id, host, port, user, ttl, limit_per_minute, limit_per_hour, limit_per_day, limit_behavior, use_tls, created_at FROM accounts"
            ) as cur:
                rows = await cur.fetchall()
                cols = [c[0] for c in cur.description]
                result = [dict(zip(cols, r)) for r in rows]
                for acc in result:
                    if "use_tls" in acc:
                        acc["use_tls"] = bool(acc["use_tls"]) if acc["use_tls"] is not None else None
                return result

    async def delete_account(self, account_id: str) -> None:
        """Remove a previously stored SMTP account and related state."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM accounts WHERE id=?", (account_id,))
            await db.execute("DELETE FROM pending_messages WHERE account_id=?", (account_id,))
            await db.execute("DELETE FROM deferred_messages WHERE account_id=?", (account_id,))
            await db.execute("DELETE FROM send_log WHERE account_id=?", (account_id,))
            await db.execute("DELETE FROM queued_messages WHERE account_id=?", (account_id,))
            await db.commit()

    async def get_account(self, account_id: str) -> Dict[str, Any]:
        """Fetch a single SMTP account or raise if it does not exist."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT * FROM accounts WHERE id=?", (account_id,)) as cur:
                row = await cur.fetchone()
                if not row:
                    raise ValueError(f"Account '{account_id}' not found")
                cols = [c[0] for c in cur.description]
                acc = dict(zip(cols, row))
                if "use_tls" in acc:
                    acc["use_tls"] = bool(acc["use_tls"]) if acc["use_tls"] is not None else None
                return acc

    # Pending
    async def add_pending(self, msg_id: str, to_addr: str, subject: str, account_id: str | None) -> None:
        """Track a message currently in-flight."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO pending_messages (id, to_addr, subject, account_id) VALUES (?, ?, ?, ?)",
                (msg_id, to_addr, subject, account_id),
            )
            await db.commit()

    async def remove_pending(self, msg_id: str) -> bool:
        """Remove a message from the pending queue."""
        if not msg_id:
            return False
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("DELETE FROM pending_messages WHERE id=?", (msg_id,))
            await db.commit()
            return cursor.rowcount > 0

    async def list_pending(self) -> List[Dict[str, Any]]:
        """Return pending messages along with their metadata."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT * FROM pending_messages") as cur:
                rows = await cur.fetchall()
                cols = [c[0] for c in cur.description]
                return [dict(zip(cols, r)) for r in rows]

    async def remove_pending_by_account(self, account_id: str) -> None:
        """Remove all pending messages associated with the given account."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM pending_messages WHERE account_id=?", (account_id,))
            await db.commit()

    # Deferred
    async def set_deferred(self, msg_id: str, account_id: str, deferred_until: int) -> None:
        """Store information about a deferred message."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO deferred_messages (id, account_id, deferred_until) VALUES (?, ?, ?)",
                (msg_id, account_id, deferred_until),
            )
            await db.commit()

    async def get_deferred_until(self, msg_id: str, account_id: str) -> int | None:
        """Return the defer-until timestamp for a message if present."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT deferred_until FROM deferred_messages WHERE id=? AND account_id=?",
                (msg_id, account_id)
            ) as cur:
                row = await cur.fetchone()
                return int(row[0]) if row else None

    async def clear_deferred(self, msg_id: str) -> bool:
        """Remove any deferred entry for the given message."""
        if not msg_id:
            return False
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("DELETE FROM deferred_messages WHERE id=?", (msg_id,))
            await db.commit()
            return cursor.rowcount > 0

    async def list_deferred(self) -> List[Dict[str, Any]]:
        """Return all messages currently deferred by the rate limiter."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT * FROM deferred_messages") as cur:
                rows = await cur.fetchall()
                cols = [c[0] for c in cur.description]
                return [dict(zip(cols, r)) for r in rows]

    # Queued messages (monitoring)
    async def save_message(self, msg_id: str, payload: Dict[str, Any], priority: int, priority_label: Optional[str] = None) -> None:
        """Store or refresh the payload of a message currently queued for delivery."""
        if not msg_id:
            msg_id = str(uuid.uuid4())
        data = json.dumps(payload)
        account_id = payload.get("account_id")
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO queued_messages (id, payload, priority, priority_label, account_id, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'queued', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE SET
                    payload=excluded.payload,
                    priority=excluded.priority,
                    priority_label=excluded.priority_label,
                    account_id=excluded.account_id,
                    status='queued',
                    updated_at=CURRENT_TIMESTAMP
                """,
                (msg_id, data, int(priority), priority_label, account_id),
            )
            await db.commit()

    async def update_message_status(self, msg_id: str, status: str) -> None:
        """Update the lifecycle status of a queued message."""
        if not msg_id:
            return
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE queued_messages
                SET status=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (status, msg_id),
            )
            await db.commit()

    async def delete_message(self, msg_id: str) -> bool:
        """Remove a message from the queue tracking table."""
        if not msg_id:
            return False
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("DELETE FROM queued_messages WHERE id=?", (msg_id,))
            await db.commit()
            return cursor.rowcount > 0

    async def list_messages(self, active_only: bool = False) -> List[Dict[str, Any]]:
        """Return queued messages along with their original payload."""
        async with aiosqlite.connect(self.db_path) as db:
            query = """
                SELECT id, payload, priority, priority_label, account_id, status, created_at, updated_at
                FROM queued_messages
            """
            if active_only:
                query += " WHERE status IN ('queued','pending','deferred')"
            async with db.execute(query) as cur:
                rows = await cur.fetchall()
                cols = [c[0] for c in cur.description]
                records = []
                for row in rows:
                    record = dict(zip(cols, row))
                    record["message"] = json.loads(record.pop("payload"))
                    records.append(record)
                return records

    # Send log (for rate limits)
    async def log_send(self, account_id: str, timestamp: int) -> None:
        """Record a delivery event for rate limiting purposes."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("INSERT INTO send_log (account_id, timestamp) VALUES (?, ?)", (account_id, timestamp))
            await db.commit()

    async def count_sends_since(self, account_id: str, since_ts: int) -> int:
        """Count messages sent after ``since_ts`` for the given account."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT COUNT(*) FROM send_log WHERE account_id=? AND timestamp > ?", (account_id, since_ts)) as cur:
                row = await cur.fetchone()
                return int(row[0] if row else 0)

    # Delivery reports
    async def save_delivery_report(self, event: Dict[str, Any]) -> str:
        """Persist a delivery event and return its storage identifier."""
        report_id = f"{uuid.uuid4().hex}"
        payload = json.dumps(event)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO delivery_reports (id, payload, retry_count) VALUES (?, ?, 0)",
                (report_id, payload),
            )
            await db.commit()
        return report_id

    async def list_delivery_reports(self) -> List[Dict[str, Any]]:
        """Return all persisted delivery reports awaiting transmission."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT id, payload, retry_count FROM delivery_reports ORDER BY created_at ASC") as cur:
                rows = await cur.fetchall()
        reports: List[Dict[str, Any]] = []
        for row in rows:
            report_id, payload, retry_count = row
            try:
                decoded = json.loads(payload)
            except json.JSONDecodeError:
                decoded = {"raw_payload": payload}
            reports.append({"id": report_id, "payload": decoded, "retry_count": retry_count})
        return reports

    async def delete_delivery_report(self, report_id: str) -> None:
        """Delete a delivery report after successful transmission."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM delivery_reports WHERE id=?", (report_id,))
            await db.commit()

    async def increment_report_retry(self, report_id: str) -> None:
        """Increment the retry counter for a delivery report."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE delivery_reports SET retry_count = retry_count + 1 WHERE id=?",
                (report_id,),
            )
            await db.commit()

    # Scheduler rules
    async def list_rules(self) -> List[Dict[str, Any]]:
        """Return scheduling rules ordered by priority."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT id, name, enabled, priority, days, start_hour, end_hour, cross_midnight, interval_minutes "
                "FROM schedule_rules ORDER BY priority ASC, id ASC"
            ) as cur:
                rows = await cur.fetchall()
                cols = [c[0] for c in cur.description]
                result = []
                for row in rows:
                    data = dict(zip(cols, row))
                    data["enabled"] = bool(data["enabled"])
                    data["cross_midnight"] = bool(data["cross_midnight"])
                    data["days"] = [int(x) for x in data["days"].split(",")] if data["days"] else []
                    result.append(data)
                return result

    async def add_rule(self, rule: Dict[str, Any]) -> Dict[str, Any]:
        """Insert a new scheduling rule and return the stored representation."""
        days = ",".join(str(int(d)) for d in rule.get("days", []))
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO schedule_rules (name, enabled, priority, days, start_hour, end_hour, cross_midnight, interval_minutes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    rule.get("name"),
                    1 if rule.get("enabled", True) else 0,
                    int(rule.get("priority", 0)),
                    days,
                    rule.get("start_hour"),
                    rule.get("end_hour"),
                    1 if rule.get("cross_midnight") else 0,
                    int(rule["interval_minutes"]),
                ),
            )
            await db.commit()
        rules = await self.list_rules()
        return rules[-1]

    async def delete_rule(self, rule_id: int) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM schedule_rules WHERE id=?", (rule_id,))
            await db.commit()

    async def set_rule_enabled(self, rule_id: int, enabled: bool) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE schedule_rules SET enabled=? WHERE id=?", (1 if enabled else 0, rule_id))
            await db.commit()

    async def clear_rules(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM schedule_rules")
            await db.commit()
