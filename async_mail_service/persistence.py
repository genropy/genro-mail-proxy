"""SQLite backed persistence used by the mail dispatcher."""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import aiosqlite

# Special volumes that are always available without DB configuration
SPECIAL_VOLUMES = {"base64"}


class Persistence:
    """Helper class responsible for reading and writing service state."""

    def __init__(self, db_path: str = "/data/mail_service.db"):
        """Persist data to the given database path (``:memory:`` allowed)."""
        self.db_path = db_path or ":memory:"

    async def init_db(self) -> None:
        """Create (or migrate) the database schema."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
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
                """
            )
            try:
                await db.execute("ALTER TABLE accounts ADD COLUMN use_tls INTEGER")
            except aiosqlite.OperationalError:
                pass
            try:
                await db.execute("ALTER TABLE accounts ADD COLUMN batch_size INTEGER")
            except aiosqlite.OperationalError:
                pass

            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS send_log (
                    account_id TEXT,
                    timestamp INTEGER
                )
                """
            )

            await db.execute("DROP TABLE IF EXISTS pending_messages")
            await db.execute("DROP TABLE IF EXISTS deferred_messages")
            await db.execute("DROP TABLE IF EXISTS delivery_reports")
            await db.execute("DROP TABLE IF EXISTS queued_messages")

            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    account_id TEXT,
                    priority INTEGER NOT NULL DEFAULT 2,
                    payload TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    deferred_ts INTEGER,
                    sent_ts INTEGER,
                    error_ts INTEGER,
                    error TEXT,
                    reported_ts INTEGER
                )
                """
            )

            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS volumes (
                    id TEXT NOT NULL,
                    account_id TEXT,
                    storage_type TEXT NOT NULL,
                    config TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (id, account_id)
                )
                """
            )

            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_volumes_account ON volumes(account_id)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_volumes_id ON volumes(id)"
            )

            await db.commit()

    # Accounts -----------------------------------------------------------------
    async def add_account(self, acc: Dict[str, Any]) -> None:
        """Insert or overwrite an SMTP account definition."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO accounts
                (id, host, port, user, password, ttl, limit_per_minute, limit_per_hour, limit_per_day, limit_behavior, use_tls, batch_size)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    acc["id"],
                    acc["host"],
                    int(acc["port"]),
                    acc.get("user"),
                    acc.get("password"),
                    int(acc.get("ttl", 300)),
                    acc.get("limit_per_minute"),
                    acc.get("limit_per_hour"),
                    acc.get("limit_per_day"),
                    acc.get("limit_behavior", "defer"),
                    None if acc.get("use_tls") is None else (1 if acc.get("use_tls") else 0),
                    acc.get("batch_size"),
                ),
            )
            await db.commit()

    async def list_accounts(self) -> List[Dict[str, Any]]:
        """Return all known SMTP accounts."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT id, host, port, user, ttl, limit_per_minute, limit_per_hour,
                       limit_per_day, limit_behavior, use_tls, batch_size, created_at
                FROM accounts
                """
            ) as cur:
                rows = await cur.fetchall()
                cols = [c[0] for c in cur.description]
        result = [dict(zip(cols, row)) for row in rows]
        for acc in result:
            if "use_tls" in acc:
                acc["use_tls"] = bool(acc["use_tls"]) if acc["use_tls"] is not None else None
        return result

    async def delete_account(self, account_id: str) -> None:
        """Remove a previously stored SMTP account and related state."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM accounts WHERE id=?", (account_id,))
            await db.execute("DELETE FROM messages WHERE account_id=?", (account_id,))
            await db.execute("DELETE FROM send_log WHERE account_id=?", (account_id,))
            await db.commit()

    async def get_account(self, account_id: str) -> Dict[str, Any]:
        """Fetch a single SMTP account or raise if it does not exist."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT * FROM accounts WHERE id=?", (account_id,)) as cur:
                row = await cur.fetchone()
                if not row:
                    raise ValueError(f"Account '{account_id}' not found")
                cols = [c[0] for c in cur.description]
                account = dict(zip(cols, row))
        if "use_tls" in account:
            account["use_tls"] = bool(account["use_tls"]) if account["use_tls"] is not None else None
        return account

    # Messages -----------------------------------------------------------------
    @staticmethod
    def _decode_message_row(row: Tuple[Any, ...], columns: Sequence[str]) -> Dict[str, Any]:
        data = dict(zip(columns, row))
        payload = data.pop("payload", None)
        if payload is not None:
            try:
                data["message"] = json.loads(payload)
            except json.JSONDecodeError:
                data["message"] = {"raw_payload": payload}
        else:
            data["message"] = None
        return data

    async def insert_messages(self, entries: Sequence[Dict[str, Any]]) -> List[str]:
        """Persist a batch of messages, returning the ids that were stored.

        If a message with the same id already exists but has NOT been sent (sent_ts IS NULL),
        it will be replaced with the new data. This allows clients to correct errors or
        retry with different parameters. Messages that have been sent are never replaced.
        """
        if not entries:
            return []
        inserted: List[str] = []
        async with aiosqlite.connect(self.db_path) as db:
            for entry in entries:
                msg_id = entry["id"]
                payload = json.dumps(entry["payload"])
                account_id = entry.get("account_id")
                priority = int(entry.get("priority", 2))
                deferred_ts = entry.get("deferred_ts")

                cursor = await db.execute(
                    """
                    INSERT INTO messages (id, account_id, priority, payload, deferred_ts)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        account_id = excluded.account_id,
                        priority = excluded.priority,
                        payload = excluded.payload,
                        deferred_ts = excluded.deferred_ts,
                        error_ts = NULL,
                        error = NULL,
                        reported_ts = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE sent_ts IS NULL
                    """,
                    (msg_id, account_id, priority, payload, deferred_ts),
                )

                # Check if operation succeeded (INSERT or UPDATE)
                if cursor.rowcount:
                    inserted.append(msg_id)
            await db.commit()
        return inserted

    async def fetch_ready_messages(self, *, limit: int, now_ts: int) -> List[Dict[str, Any]]:
        """Return messages eligible for SMTP dispatch."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT id, account_id, priority, payload, deferred_ts
                FROM messages
                WHERE sent_ts IS NULL
                  AND error_ts IS NULL
                  AND (deferred_ts IS NULL OR deferred_ts <= ?)
                ORDER BY priority ASC, created_at ASC, id ASC
                LIMIT ?
                """,
                (now_ts, limit),
            ) as cur:
                rows = await cur.fetchall()
                cols = [c[0] for c in cur.description]
        return [self._decode_message_row(row, cols) for row in rows]

    async def set_deferred(self, msg_id: str, deferred_ts: int) -> None:
        """Update the deferred timestamp for a message."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE messages
                SET deferred_ts=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND sent_ts IS NULL AND error_ts IS NULL
                """,
                (deferred_ts, msg_id),
            )
            await db.commit()

    async def clear_deferred(self, msg_id: str) -> None:
        """Clear the deferred timestamp for a message."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE messages
                SET deferred_ts=NULL, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (msg_id,),
            )
            await db.commit()

    async def mark_sent(self, msg_id: str, sent_ts: int) -> None:
        """Mark a message as sent.

        Resets reported_ts so the message will be reported with final state.
        """
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE messages
                SET sent_ts=?, error_ts=NULL, error=NULL, deferred_ts=NULL, reported_ts=NULL, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (sent_ts, msg_id),
            )
            await db.commit()

    async def mark_error(self, msg_id: str, error_ts: int, error: str) -> None:
        """Mark a message as failed.

        Resets reported_ts and deferred_ts so the message will be reported with final error state.
        """
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE messages
                SET error_ts=?, error=?, sent_ts=NULL, deferred_ts=NULL, reported_ts=NULL, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (error_ts, error, msg_id),
            )
            await db.commit()

    async def update_message_payload(self, msg_id: str, payload: Dict[str, Any]) -> None:
        """Update the payload field of a message (used for retry count tracking)."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE messages
                SET payload=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (json.dumps(payload), msg_id),
            )
            await db.commit()

    async def delete_message(self, msg_id: str) -> bool:
        """Remove a message regardless of its state."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("DELETE FROM messages WHERE id=?", (msg_id,))
            await db.commit()
            return cursor.rowcount > 0

    async def purge_messages_for_account(self, account_id: str) -> None:
        """Delete every message linked to the given account."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM messages WHERE account_id=?", (account_id,))
            await db.commit()

    async def existing_message_ids(self, ids: Iterable[str]) -> set[str]:
        """Return the subset of ids that already exist in storage."""
        id_list = [mid for mid in ids if mid]
        if not id_list:
            return set()
        placeholders = ",".join("?" for _ in id_list)
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                f"SELECT id FROM messages WHERE id IN ({placeholders})",
                id_list,
            ) as cur:
                rows = await cur.fetchall()
        return {row[0] for row in rows}

    async def fetch_reports(self, limit: int) -> List[Dict[str, Any]]:
        """Return messages that need to be reported back to the client.

        Only returns messages in final states (sent or error).
        Messages with only deferred_ts are not reported (internal retry logic).
        """
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT id, account_id, priority, payload, sent_ts, error_ts, error, deferred_ts
                FROM messages
                WHERE reported_ts IS NULL
                  AND (sent_ts IS NOT NULL OR error_ts IS NOT NULL)
                ORDER BY updated_at ASC, id ASC
                LIMIT ?
                """,
                (limit,),
            ) as cur:
                rows = await cur.fetchall()
                cols = [c[0] for c in cur.description]
        return [self._decode_message_row(row, cols) for row in rows]

    async def mark_reported(self, message_ids: Iterable[str], reported_ts: int) -> None:
        """Set the reported timestamp for the provided messages."""
        ids = [mid for mid in message_ids if mid]
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                f"""
                UPDATE messages
                SET reported_ts=?, updated_at=CURRENT_TIMESTAMP
                WHERE id IN ({placeholders})
                """,
                (reported_ts, *ids),
            )
            await db.commit()

    async def remove_reported_before(self, threshold_ts: int) -> int:
        """Delete reported messages older than ``threshold_ts``.

        Only deletes messages in final states (sent or error).
        Messages with only deferred_ts are kept in queue until they reach a final state.
        """
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                DELETE FROM messages
                WHERE reported_ts IS NOT NULL
                  AND reported_ts < ?
                  AND (sent_ts IS NOT NULL OR error_ts IS NOT NULL)
                """,
                (threshold_ts,),
            )
            await db.commit()
            return cursor.rowcount

    async def list_messages(self, *, active_only: bool = False) -> List[Dict[str, Any]]:
        """Return messages for inspection purposes."""
        query = """
            SELECT id, account_id, priority, payload, deferred_ts, sent_ts, error_ts,
                   error, reported_ts, created_at, updated_at
            FROM messages
        """
        params: Tuple[Any, ...] = ()
        if active_only:
            query += " WHERE sent_ts IS NULL AND error_ts IS NULL"
        query += " ORDER BY priority ASC, created_at ASC, id ASC"
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(query, params) as cur:
                rows = await cur.fetchall()
                cols = [c[0] for c in cur.description]
        return [self._decode_message_row(row, cols) for row in rows]

    async def count_active_messages(self) -> int:
        """Return the number of messages still awaiting delivery."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT COUNT(*) FROM messages
                WHERE sent_ts IS NULL AND error_ts IS NULL
                """
            ) as cur:
                row = await cur.fetchone()
        return int(row[0] if row else 0)

    # Send log -----------------------------------------------------------------
    async def log_send(self, account_id: str, timestamp: int) -> None:
        """Record a delivery event for rate limiting purposes."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("INSERT INTO send_log (account_id, timestamp) VALUES (?, ?)", (account_id, timestamp))
            await db.commit()

    async def count_sends_since(self, account_id: str, since_ts: int) -> int:
        """Count messages sent after ``since_ts`` for the given account."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT COUNT(*) FROM send_log WHERE account_id=? AND timestamp > ?",
                (account_id, since_ts),
            ) as cur:
                row = await cur.fetchone()
        return int(row[0] if row else 0)

    # Volumes ------------------------------------------------------------------
    async def add_volumes(self, volumes: List[Dict[str, Any]]) -> None:
        """Insert or replace storage volumes. account_id can be None for global volumes."""
        if not volumes:
            return
        async with aiosqlite.connect(self.db_path) as db:
            for vol in volumes:
                account_id = vol.get("account_id")  # Can be None for global volumes
                await db.execute(
                    """
                    INSERT OR REPLACE INTO volumes (id, account_id, storage_type, config, updated_at)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (vol["id"], account_id, vol["storage_type"], json.dumps(vol["config"]))
                )
            await db.commit()

    async def list_volumes(self, account_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return volumes accessible by account_id.

        If account_id is None, returns ALL volumes.
        If account_id is provided, returns volumes specific to that account plus global volumes.
        """
        async with aiosqlite.connect(self.db_path) as db:
            if account_id is None:
                # List all volumes (admin view)
                query = "SELECT id, account_id, storage_type, config, created_at, updated_at FROM volumes"
                params = ()
            else:
                # List volumes accessible by this account (specific + global)
                query = """
                    SELECT id, account_id, storage_type, config, created_at, updated_at
                    FROM volumes
                    WHERE account_id = ? OR account_id IS NULL
                """
                params = (account_id,)

            async with db.execute(query, params) as cur:
                rows = await cur.fetchall()
                cols = [c[0] for c in cur.description]

        result = []
        for row in rows:
            vol = dict(zip(cols, row))
            vol["config"] = json.loads(vol["config"])
            result.append(vol)
        return result

    async def get_volume(self, volume_id: str, account_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Get volume configuration accessible by account_id.

        Lookup order:
        1. Volume specific to account_id (if provided)
        2. Global volume (account_id IS NULL)

        Returns None if no volume found.
        """
        # Special volumes are always available
        if volume_id in SPECIAL_VOLUMES:
            return {
                "id": volume_id,
                "account_id": None,
                "storage_type": "memory",
                "config": {"type": volume_id}
            }

        async with aiosqlite.connect(self.db_path) as db:
            # Try account-specific volume first
            if account_id:
                async with db.execute(
                    "SELECT id, account_id, storage_type, config FROM volumes WHERE id=? AND account_id=?",
                    (volume_id, account_id)
                ) as cur:
                    row = await cur.fetchone()
                    if row:
                        cols = [c[0] for c in cur.description]
                        vol = dict(zip(cols, row))
                        vol["config"] = json.loads(vol["config"])
                        return vol

            # Try global volume
            async with db.execute(
                "SELECT id, account_id, storage_type, config FROM volumes WHERE id=? AND account_id IS NULL",
                (volume_id,)
            ) as cur:
                row = await cur.fetchone()
                if not row:
                    return None
                cols = [c[0] for c in cur.description]
                vol = dict(zip(cols, row))
                vol["config"] = json.loads(vol["config"])
                return vol

    async def delete_volume(self, volume_id: str, account_id: Optional[str] = None) -> bool:
        """Delete a volume. Returns True if deleted.

        If account_id is None, deletes the global volume.
        Otherwise deletes the account-specific volume.
        """
        async with aiosqlite.connect(self.db_path) as db:
            if account_id is None:
                # Delete global volume
                cursor = await db.execute(
                    "DELETE FROM volumes WHERE id=? AND account_id IS NULL",
                    (volume_id,)
                )
            else:
                # Delete account-specific volume
                cursor = await db.execute(
                    "DELETE FROM volumes WHERE id=? AND account_id=?",
                    (volume_id, account_id)
                )
            await db.commit()
            return cursor.rowcount > 0

    async def validate_storage_paths(self, storage_paths: List[str], account_id: Optional[str]) -> Dict[str, bool]:
        """Validate that all storage paths have configured volumes.

        Returns dict mapping storage_path -> is_valid.
        Special volumes (like 'base64') are always valid.
        """
        if not storage_paths:
            return {}

        # Extract volume IDs from storage paths
        volume_ids = set()
        for path in storage_paths:
            if ":" in path:
                volume_id = path.split(":", 1)[0]
                volume_ids.add(volume_id)

        if not volume_ids:
            return {path: False for path in storage_paths}

        # Check which volumes exist
        results = {}
        for volume_id in volume_ids:
            # Special volumes are always valid
            if volume_id in SPECIAL_VOLUMES:
                results[volume_id] = True
                continue

            # Regular volumes: check DB
            vol = await self.get_volume(volume_id, account_id)
            results[volume_id] = vol is not None

        # Map back to storage paths
        return {
            path: results.get(path.split(":", 1)[0], False) if ":" in path else False
            for path in storage_paths
        }

