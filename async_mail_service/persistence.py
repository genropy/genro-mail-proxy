"""SQLite backed persistence used by the mail dispatcher."""

import aiosqlite
from typing import List, Dict, Any

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
                    started_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
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
        """Remove a previously stored SMTP account."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM accounts WHERE id=?", (account_id,))
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
    async def add_pending(self, msg_id: str, to_addr: str, subject: str) -> None:
        """Track a message currently in-flight."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO pending_messages (id, to_addr, subject) VALUES (?, ?, ?)",
                (msg_id, to_addr, subject),
            )
            await db.commit()

    async def remove_pending(self, msg_id: str) -> None:
        """Remove a message from the pending queue."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM pending_messages WHERE id=?", (msg_id,))
            await db.commit()

    async def list_pending(self) -> List[Dict[str, Any]]:
        """Return pending messages along with their metadata."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT * FROM pending_messages") as cur:
                rows = await cur.fetchall()
                cols = [c[0] for c in cur.description]
                return [dict(zip(cols, r)) for r in rows]

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

    async def clear_deferred(self, msg_id: str) -> None:
        """Remove any deferred entry for the given message."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM deferred_messages WHERE id=?", (msg_id,))
            await db.commit()

    async def list_deferred(self) -> List[Dict[str, Any]]:
        """Return all messages currently deferred by the rate limiter."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT * FROM deferred_messages") as cur:
                rows = await cur.fetchall()
                cols = [c[0] for c in cur.description]
                return [dict(zip(cols, r)) for r in rows]

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
