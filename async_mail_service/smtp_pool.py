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
        # Use explicit 10 second timeout to prevent hanging connections
        # For plain SMTP (use_tls=False): both use_tls and start_tls should be False
        # For TLS/SSL (use_tls=True): use_tls=True, start_tls=False (direct TLS on port 465)
        smtp = aiosmtplib.SMTP(hostname=host, port=port, start_tls=False, use_tls=use_tls, timeout=10.0)
        # Wrap in asyncio.wait_for to ensure we don't hang even if aiosmtplib timeout fails
        async def _do_connect():
            await smtp.connect()
            if user and password:
                await smtp.login(user, password)
        await asyncio.wait_for(_do_connect(), timeout=15.0)
        return smtp

    async def _is_alive(self, smtp: aiosmtplib.SMTP) -> bool:
        """Return ``True`` when the connection responds correctly to NOOP."""
        try:
            # Use timeout to prevent hanging on dead connections
            code, _ = await asyncio.wait_for(smtp.noop(), timeout=5.0)
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
            params_match = params == (host, port, user, password, use_tls)
            fresh_enough = (time.time() - last_used) < self.ttl

            if params_match and fresh_enough:
                is_alive = await self._is_alive(smtp)
                if is_alive:
                    async with self.lock:
                        self.pool[task_id] = (smtp, time.time(), params)
                    return smtp
            async with self.lock:
                self.pool.pop(task_id, None)
            try:
                await smtp.quit()
            except Exception:
                pass

        smtp = await self._connect(host, port, user, password, use_tls)
        async with self.lock:
            self.pool[task_id] = (smtp, time.time(), (host, port, user, password, use_tls))
        return smtp

    async def cleanup(self) -> None:
        """Close idle or broken connections still registered in the pool."""
        now = time.time()
        async with self.lock:
            items = list(self.pool.items())

        expired: list[Tuple[int, aiosmtplib.SMTP]] = []
        candidates: list[Tuple[int, aiosmtplib.SMTP]] = []
        for task_id, (smtp, last_used, params) in items:
            if (now - last_used) > self.ttl:
                expired.append((task_id, smtp))
            else:
                candidates.append((task_id, smtp))

        for task_id, smtp in candidates:
            try:
                alive = await self._is_alive(smtp)
            except Exception:
                alive = False
            if not alive:
                expired.append((task_id, smtp))

        for task_id, smtp in expired:
            async with self.lock:
                entry = self.pool.pop(task_id, None)
            if entry:
                try:
                    await entry[0].quit()
                except Exception:
                    pass
