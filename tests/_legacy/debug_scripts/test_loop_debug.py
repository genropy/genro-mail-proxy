#!/usr/bin/env python3
"""Debug script to test SMTP loop directly."""

import asyncio
import sqlite3

from core.mail_proxy.mailproxy_db import MailProxyDb


async def main():
    db_path = "/tmp/mail_service.db"
    persistence = MailProxyDb(db_path)

    now_ts = int(asyncio.get_event_loop().time())

    print(f"🕐 Current timestamp: {now_ts}")
    print(f"📂 Database: {db_path}")
    print()

    # Check what fetch_ready_messages returns
    messages = await persistence.table('messages').fetch_ready(limit=10, now_ts=now_ts)

    print(f"✅ fetch_ready_messages returned {len(messages)} messages:")
    for msg in messages:
        print(f"  - ID: {msg['id']}")
        print(f"    Account: {msg.get('account_id')}")
        print(f"    Priority: {msg.get('priority')}")
        print(f"    Deferred: {msg.get('deferred_ts')}")
        print()

    # Also check directly in database
    conn = sqlite3.connect(db_path)
    cursor = conn.execute("""
        SELECT id, account_id, priority, sent_ts, error_ts, deferred_ts
        FROM messages
        WHERE sent_ts IS NULL AND error_ts IS NULL
        AND (deferred_ts IS NULL OR deferred_ts <= ?)
        ORDER BY priority ASC, created_at ASC
        LIMIT 10
    """, (now_ts,))

    print("📊 Direct database query:")
    rows = cursor.fetchall()
    print(f"Found {len(rows)} rows:")
    for row in rows:
        print(f"  - ID: {row[0]}, Account: {row[1]}, Priority: {row[2]}")
        print(f"    sent_ts: {row[3]}, error_ts: {row[4]}, deferred_ts: {row[5]}")

    conn.close()

if __name__ == "__main__":
    asyncio.run(main())
