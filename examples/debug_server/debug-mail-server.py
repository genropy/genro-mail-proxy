#!/usr/bin/env python3

"""
A simple smtp server based on aiosmtpd, to inspect
what the smtp_pool is really doing at network and messages level.

This implementation just prints out connection, disconnection, and received messages.

Please change LISTEN_IP and LISTEN_PORT to suit your needs.
"""

import asyncio
from aiosmtpd.controller import Controller

LISTEN_IP = '127.0.0.1'
LISTEN_PORT = 1587

class DebugHandler:
    async def handle_CONNECT(self, server, session, envelope, hostname, port):
        print(f"[*] Connection opened from {hostname}:{port}")
        return None  

    async def handle_QUIT(self, server, session, envelope):
        print(f"[*] Connection closed from {session.peer[0]}:{session.peer[1]}")
        return '221 Bye'
    
    async def handle_DATA(self, server, session, envelope):
        print("=" * 60)
        print(f"Mail From: {envelope.mail_from}")
        print(f"Mail To: {', '.join(envelope.rcpt_tos)}")
        print("-" * 60)
        print(envelope.content.decode(errors="replace"))
        print("=" * 60)
        return '250 Message accepted for delivery'

async def main():
    controller = Controller(DebugHandler(), 
                            hostname=LISTEN_IP,
                            port=LISTEN_PORT)
    controller.start()
    
    print(f"Debug SMTP server running on {LISTEN_IP}:{LISTEN_PORT}")
    
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        pass
    
    finally:
        controller.stop()

if __name__ == "__main__":
    asyncio.run(main())
