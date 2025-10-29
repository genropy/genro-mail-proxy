#!/usr/bin/env python3
"""
Script per verificare se il loop SMTP sta effettivamente processando messaggi
"""

import asyncio
import aiosqlite

DB_PATH = "/tmp/mail_service.db"

async def check_loop():
    print("🔍 Controllo attività loop SMTP...\n")

    # Prendi timestamp dei pending
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT id, updated_at, deferred_ts, sent_ts, error_ts
            FROM messages
            WHERE sent_ts IS NULL AND error_ts IS NULL
            ORDER BY created_at ASC
        """) as cur:
            before = await cur.fetchall()

    print(f"📊 Messaggi pending: {len(before)}")
    for msg_id, updated, def_ts, sent, err in before:
        print(f"  {msg_id[:30]}: updated={updated}, deferred={def_ts}")

    print("\n⏳ Attendo 5 secondi...")
    await asyncio.sleep(5)

    # Ricontrolla
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT id, updated_at, deferred_ts, sent_ts, error_ts
            FROM messages
            WHERE sent_ts IS NULL AND error_ts IS NULL
            ORDER BY created_at ASC
        """) as cur:
            after = await cur.fetchall()

    print(f"\n📊 Messaggi pending dopo 5s: {len(after)}")
    for msg_id, updated, def_ts, sent, err in after:
        print(f"  {msg_id[:30]}: updated={updated}, deferred={def_ts}")

    # Controlla se updated_at è cambiato
    print("\n🔎 ANALISI:")
    if len(before) != len(after):
        print(f"  ✅ Numero messaggi cambiato: {len(before)} → {len(after)}")
        print(f"  → Loop sta processando!")
    else:
        changed = False
        for b, a in zip(before, after):
            if b[1] != a[1]:  # updated_at changed
                print(f"  ✅ Messaggio {b[0][:30]} updated_at cambiato")
                print(f"     PRIMA: {b[1]}")
                print(f"     DOPO:  {a[1]}")
                changed = True
            if b[2] != a[2]:  # deferred_ts changed
                print(f"  ⚠️  Messaggio {b[0][:30]} deferred_ts cambiato")
                print(f"     PRIMA: {b[2]}")
                print(f"     DOPO:  {a[2]}")
                changed = True

        if not changed:
            print("  ❌ Nessun cambiamento rilevato!")
            print("  → Il loop potrebbe NON star girando")
            print("  → O potrebbe esserci un errore che blocca l'invio")

    # Controlla se ci sono stati nuovi invii
    print("\n📤 Ultimi 3 invii registrati:")
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT account_id, timestamp, datetime(timestamp, 'unixepoch')
            FROM send_log
            ORDER BY timestamp DESC
            LIMIT 3
        """) as cur:
            logs = await cur.fetchall()
            for log in logs:
                print(f"  {log[0]}: {log[2]} (ts={log[1]})")

if __name__ == "__main__":
    asyncio.run(check_loop())
