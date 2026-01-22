# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Lightweight asyncio-friendly SMTP connection pool.

This module provides a connection pool for SMTP clients, enabling efficient
reuse of SMTP connections across multiple send operations. Connections are
keyed by asyncio task ID, allowing concurrent tasks to maintain separate
connections while minimizing the overhead of repeated connection establishment.

The pool automatically handles connection lifecycle management including:
- TTL-based connection expiration
- Health checking via SMTP NOOP commands
- Automatic reconnection when connections become stale or broken
- Graceful cleanup of expired connections

Example:
    Using the SMTP pool for email sending::

        pool = SMTPPool(ttl=300)

        # Get a connection (creates new or reuses existing)
        smtp = await pool.get_connection(
            host="smtp.example.com",
            port=465,
            user="sender@example.com",
            password="secret",
            use_tls=True
        )

        # Send message using the connection
        await smtp.send_message(message)

        # Periodically clean up stale connections
        await pool.cleanup()
"""

import asyncio
import time

import aiosmtplib


class SMTPPool:
    """Asyncio-compatible SMTP connection pool with per-task connection reuse.

    Maintains a pool of SMTP connections indexed by asyncio task ID, enabling
    each concurrent task to efficiently reuse its own connection across
    multiple send operations. Connections are validated before reuse and
    automatically replaced when they expire or fail health checks.

    The pool uses a time-to-live (TTL) mechanism to prevent connections from
    becoming stale, and performs SMTP NOOP checks to verify connection health
    before returning pooled connections.

    Attributes:
        ttl: Maximum age in seconds for pooled connections before expiration.
        pool: Internal dictionary mapping task IDs to connection entries.
        lock: Asyncio lock for thread-safe pool access.
    """

    def __init__(self, ttl: int = 300):
        """Initialize the SMTP connection pool.

        Args:
            ttl: Time-to-live in seconds for pooled connections. Connections
                older than this value are considered expired and will be
                replaced on the next access. Defaults to 300 seconds (5 minutes).
        """
        self.ttl = ttl
        self.pool: dict[int, tuple[aiosmtplib.SMTP, float, tuple[str, int, str | None, str | None, bool]]] = {}
        self.lock = asyncio.Lock()

    async def _connect(self, host: str, port: int, user: str | None, password: str | None, use_tls: bool) -> aiosmtplib.SMTP:
        """Establish a new SMTP connection with optional authentication.

        Creates a new aiosmtplib SMTP client, connects to the specified server,
        and performs authentication if credentials are provided. The connection
        process is wrapped in a timeout to prevent indefinite blocking.

        TLS behavior based on port and use_tls flag:
        - Port 465 with use_tls=True: Direct TLS (implicit TLS)
        - Port 587 with use_tls=True: STARTTLS (upgrade plain to TLS)
        - use_tls=False: Plain SMTP (no encryption)

        Args:
            host: SMTP server hostname or IP address.
            port: SMTP server port number (typically 25, 465, or 587).
            user: Username for SMTP authentication, or None for no auth.
            password: Password for SMTP authentication, or None for no auth.
            use_tls: Whether to use TLS (direct TLS on 465, STARTTLS on other ports).

        Returns:
            A connected and optionally authenticated aiosmtplib.SMTP instance.

        Raises:
            asyncio.TimeoutError: If connection takes longer than 15 seconds.
            aiosmtplib.SMTPException: If connection or authentication fails.
        """
        # Use explicit 10 second timeout to prevent hanging connections
        # Port 465: Direct TLS (use_tls=True, start_tls=False)
        # Port 587 or other with TLS: STARTTLS (use_tls=False, start_tls=True)
        # No TLS: Plain connection (use_tls=False, start_tls=False)
        if use_tls and port == 465:
            # Direct TLS (implicit TLS) for port 465
            smtp = aiosmtplib.SMTP(hostname=host, port=port, start_tls=False, use_tls=True, timeout=10.0)
        elif use_tls:
            # STARTTLS for other ports (typically 587)
            smtp = aiosmtplib.SMTP(hostname=host, port=port, start_tls=True, use_tls=False, timeout=10.0)
        else:
            # Plain SMTP (no encryption)
            smtp = aiosmtplib.SMTP(hostname=host, port=port, start_tls=False, use_tls=False, timeout=10.0)
        # Wrap in asyncio.wait_for to ensure we don't hang even if aiosmtplib timeout fails
        async def _do_connect():
            await smtp.connect()
            if user and password:
                await smtp.login(user, password)
        await asyncio.wait_for(_do_connect(), timeout=15.0)
        return smtp

    async def _is_alive(self, smtp: aiosmtplib.SMTP) -> bool:
        """Check if an SMTP connection is still alive and responsive.

        Sends an SMTP NOOP command to verify the connection is functional.
        This is a lightweight health check that validates both network
        connectivity and server responsiveness without affecting state.

        Args:
            smtp: The SMTP connection to check.

        Returns:
            True if the connection responds with a 250 status code within
            the timeout period, False otherwise.
        """
        try:
            # Use timeout to prevent hanging on dead connections
            code, _ = await asyncio.wait_for(smtp.noop(), timeout=5.0)
            return code == 250
        except Exception:
            return False

    async def get_connection(self, host: str, port: int, user: str | None, password: str | None, *, use_tls: bool) -> aiosmtplib.SMTP:
        """Retrieve or create an SMTP connection for the current asyncio task.

        Returns a pooled connection if one exists for the current task with
        matching parameters and is still valid (within TTL and passing health
        check). Otherwise, creates a new connection, stores it in the pool,
        and returns it.

        Each asyncio task gets its own dedicated connection, identified by
        task ID. This prevents connection conflicts between concurrent tasks
        while enabling connection reuse across multiple sends within a task.

        Args:
            host: SMTP server hostname or IP address.
            port: SMTP server port number.
            user: Username for SMTP authentication, or None for no auth.
            password: Password for SMTP authentication, or None for no auth.
            use_tls: Whether to use direct TLS connection (keyword-only).

        Returns:
            A connected and authenticated aiosmtplib.SMTP instance ready
            for sending messages.

        Raises:
            asyncio.TimeoutError: If connection establishment times out.
            aiosmtplib.SMTPException: If connection or authentication fails.
        """
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
        """Remove and close expired or unhealthy connections from the pool.

        Iterates through all pooled connections and closes those that have
        exceeded the TTL or fail the health check. This method should be
        called periodically (e.g., by a background task) to prevent resource
        leaks from abandoned connections.

        The cleanup process:
        1. Identifies connections that have exceeded TTL
        2. Performs health checks on remaining connections
        3. Gracefully closes all invalid connections
        4. Removes entries from the pool

        Connection closure is best-effort; exceptions during quit are ignored
        to ensure complete pool cleanup even with partially failed connections.
        """
        now = time.time()
        async with self.lock:
            items = list(self.pool.items())

        expired: list[tuple[int, aiosmtplib.SMTP]] = []
        candidates: list[tuple[int, aiosmtplib.SMTP]] = []
        for task_id, (smtp, last_used, _params) in items:
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

        for task_id, _smtp in expired:
            async with self.lock:
                entry = self.pool.pop(task_id, None)
            if entry:
                try:
                    await entry[0].quit()
                except Exception:
                    pass
