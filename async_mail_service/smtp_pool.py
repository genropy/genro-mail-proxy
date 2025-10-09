"""Lightweight asyncio-friendly SMTP connection pool."""

import asyncio
import time
import aiosmtplib
from typing import Optional, Tuple, Dict

class SMTPPool:
    """Reuse SMTP connections per task to reduce connection overhead."""

    def __init__(self, ttl: int = 300):
        """Create a pool with the given time-to-live, in seconds."""
        self.ttl = ttl
        self.pool: Dict[int, Tuple[aiosmtplib.SMTP, float, Tuple[str, int, Optional[str], Optional[str], bool]]] = {}
        self.lock = asyncio.Lock()

    async def _connect(self, host: str, port: int, user: Optional[str], password: Optional[str], use_tls: bool) -> aiosmtplib.SMTP:
        """Open a new SMTP connection and authenticate if needed."""
        smtp = aiosmtplib.SMTP(hostname=host, port=port, start_tls=not use_tls, use_tls=use_tls)
        await smtp.connect()
        if user and password:
            await smtp.login(user, password)
        return smtp

    async def _is_alive(self, smtp: aiosmtplib.SMTP) -> bool:
        """Return ``True`` when the connection responds correctly to NOOP."""
        try:
            code, _ = await smtp.noop()
            return code == 250
        except Exception:
            return False

    async def get_connection(self, host: str, port: int, user: Optional[str], password: Optional[str], *, use_tls: bool) -> aiosmtplib.SMTP:
        """Return a pooled connection bound to the calling task."""
        task_id = id(asyncio.current_task())
        async with self.lock:
            entry = self.pool.get(task_id)
            if entry:
                smtp, last_used, params = entry
                if params != (host, port, user, password, use_tls):
                    try:
                        await smtp.quit()
                    except Exception:
                        pass
                    self.pool.pop(task_id, None)
                elif await self._is_alive(smtp) and (time.time() - last_used) < self.ttl:
                    self.pool[task_id] = (smtp, time.time(), params)
                    return smtp
                try:
                    await smtp.quit()
                except Exception:
                    pass
                self.pool.pop(task_id, None)

            smtp = await self._connect(host, port, user, password, use_tls)
            self.pool[task_id] = (smtp, time.time(), (host, port, user, password, use_tls))
            return smtp

    async def cleanup(self) -> None:
        """Close idle or broken connections still registered in the pool."""
        now = time.time()
        async with self.lock:
            for task_id, (smtp, last_used, params) in list(self.pool.items()):
                if (now - last_used) > self.ttl or not await self._is_alive(smtp):
                    try:
                        await smtp.quit()
                    except Exception:
                        pass
                    self.pool.pop(task_id, None)
