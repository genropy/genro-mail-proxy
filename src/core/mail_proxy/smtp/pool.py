# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Asyncio-friendly SMTP connection pool with acquire/release pattern.

This module provides a connection pool for SMTP clients using a true resource
pool pattern with acquire/release semantics. Connections are pooled by account
key (host:port:user) and can be shared between concurrent coroutines.

Features:
- Connection pooling per SMTP account
- Configurable max connections per account
- TTL-based connection expiration
- Health checking via SMTP NOOP commands
- Automatic reconnection when connections become stale
- Context manager support for automatic release

Example:
    Using the SMTP pool with context manager::

        pool = SMTPPool(ttl=300, max_per_account=5)

        async with pool.connection(
            host="smtp.example.com",
            port=465,
            user="sender@example.com",
            password="secret",
            use_tls=True
        ) as smtp:
            await smtp.send_message(message)
        # Connection automatically released back to pool

    Manual acquire/release::

        smtp = await pool.acquire(host, port, user, password, use_tls=True)
        try:
            await smtp.send_message(message)
        finally:
            await pool.release(smtp)
"""

import asyncio
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import aiosmtplib


@dataclass
class PooledConnection:
    """Wrapper for a pooled SMTP connection with metadata."""

    smtp: aiosmtplib.SMTP
    account_key: str
    created_at: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)

    def touch(self) -> None:
        """Update last_used timestamp."""
        self.last_used = time.time()

    def age(self) -> float:
        """Return connection age in seconds."""
        return time.time() - self.created_at

    def idle_time(self) -> float:
        """Return time since last use in seconds."""
        return time.time() - self.last_used


class SMTPPool:
    """SMTP connection pool with acquire/release semantics.

    Maintains pools of SMTP connections indexed by account key (host:port:user).
    Connections can be acquired by any coroutine and must be released after use.
    This enables efficient connection sharing across concurrent operations.

    The pool uses TTL-based expiration and NOOP health checks to ensure
    connections remain valid. Invalid connections are discarded and replaced.

    Attributes:
        ttl: Maximum age in seconds for pooled connections.
        max_per_account: Maximum connections per SMTP account.
    """

    def __init__(self, ttl: int = 300, max_per_account: int = 5):
        """Initialize the SMTP connection pool.

        Args:
            ttl: Time-to-live in seconds for connections. Defaults to 300.
            max_per_account: Max concurrent connections per account. Defaults to 5.
        """
        self.ttl = ttl
        self.max_per_account = max_per_account

        # Pool of idle connections: account_key -> list of PooledConnection
        self._idle: dict[str, list[PooledConnection]] = {}

        # Count of active (acquired) connections per account
        self._active_count: dict[str, int] = {}

        # Condition for waiting when pool is full
        self._conditions: dict[str, asyncio.Condition] = {}

        # Global lock for pool operations
        self._lock = asyncio.Lock()

        # Track connection -> account_key for release
        self._connection_keys: dict[int, str] = {}

    def _make_key(self, host: str, port: int, user: str | None) -> str:
        """Create account key from connection parameters."""
        return f"{host}:{port}:{user or ''}"

    async def _get_condition(self, key: str) -> asyncio.Condition:
        """Get or create condition variable for an account."""
        if key not in self._conditions:
            self._conditions[key] = asyncio.Condition()
        return self._conditions[key]

    async def _connect(
        self,
        host: str,
        port: int,
        user: str | None,
        password: str | None,
        use_tls: bool,
    ) -> aiosmtplib.SMTP:
        """Establish a new SMTP connection.

        TLS behavior:
        - Port 465 with use_tls=True: Direct TLS (implicit)
        - Port 587 with use_tls=True: STARTTLS
        - use_tls=False: Plain SMTP

        Args:
            host: SMTP server hostname.
            port: SMTP server port.
            user: Username for auth, or None.
            password: Password for auth, or None.
            use_tls: Whether to use TLS.

        Returns:
            Connected aiosmtplib.SMTP instance.

        Raises:
            asyncio.TimeoutError: If connection times out.
            aiosmtplib.SMTPException: If connection fails.
        """
        if use_tls and port == 465:
            smtp = aiosmtplib.SMTP(
                hostname=host, port=port, start_tls=False, use_tls=True, timeout=10.0
            )
        elif use_tls:
            smtp = aiosmtplib.SMTP(
                hostname=host, port=port, start_tls=True, use_tls=False, timeout=10.0
            )
        else:
            smtp = aiosmtplib.SMTP(
                hostname=host, port=port, start_tls=False, use_tls=False, timeout=10.0
            )

        async def _do_connect():
            await smtp.connect()
            if user and password:
                await smtp.login(user, password)

        await asyncio.wait_for(_do_connect(), timeout=15.0)
        return smtp

    async def _is_alive(self, smtp: aiosmtplib.SMTP) -> bool:
        """Check if connection is alive via NOOP command."""
        try:
            code, _ = await asyncio.wait_for(smtp.noop(), timeout=5.0)
            return code == 250
        except Exception:
            return False

    async def _close_connection(self, conn: PooledConnection) -> None:
        """Close a pooled connection gracefully."""
        try:
            await asyncio.wait_for(conn.smtp.quit(), timeout=5.0)
        except Exception:
            pass

    async def acquire(
        self,
        host: str,
        port: int,
        user: str | None,
        password: str | None,
        *,
        use_tls: bool,
        timeout: float | None = 30.0,
    ) -> aiosmtplib.SMTP:
        """Acquire a connection from the pool.

        Returns an idle connection if available, otherwise creates a new one.
        If max connections reached, waits until one becomes available.

        Args:
            host: SMTP server hostname.
            port: SMTP server port.
            user: Username for auth, or None.
            password: Password for auth, or None.
            use_tls: Whether to use TLS.
            timeout: Max seconds to wait for connection. None = wait forever.

        Returns:
            SMTP connection ready for use.

        Raises:
            asyncio.TimeoutError: If timeout waiting for connection.
        """
        key = self._make_key(host, port, user)
        deadline = time.time() + timeout if timeout else None

        while True:
            async with self._lock:
                # Try to get an idle connection
                if key in self._idle and self._idle[key]:
                    conn = self._idle[key].pop()

                    # Check TTL
                    if conn.age() > self.ttl:
                        await self._close_connection(conn)
                        continue

                    # Check health
                    if not await self._is_alive(conn.smtp):
                        await self._close_connection(conn)
                        continue

                    # Valid connection found
                    conn.touch()
                    self._active_count[key] = self._active_count.get(key, 0) + 1
                    self._connection_keys[id(conn.smtp)] = key
                    return conn.smtp

                # No idle connection - can we create new?
                active = self._active_count.get(key, 0)
                if active < self.max_per_account:
                    # Create new connection
                    self._active_count[key] = active + 1

            # Create connection outside lock
            if active < self.max_per_account:
                try:
                    smtp = await self._connect(host, port, user, password, use_tls)
                    async with self._lock:
                        self._connection_keys[id(smtp)] = key
                    return smtp
                except Exception:
                    async with self._lock:
                        self._active_count[key] = self._active_count.get(key, 1) - 1
                    raise

            # Pool is full - wait for release
            condition = await self._get_condition(key)
            async with condition:
                remaining = deadline - time.time() if deadline else None
                if remaining is not None and remaining <= 0:
                    raise asyncio.TimeoutError("Timeout waiting for SMTP connection")

                try:
                    await asyncio.wait_for(condition.wait(), timeout=remaining)
                except asyncio.TimeoutError:
                    raise asyncio.TimeoutError("Timeout waiting for SMTP connection")

    async def release(self, smtp: aiosmtplib.SMTP) -> None:
        """Release a connection back to the pool.

        The connection is returned to the idle pool for reuse by other
        coroutines. If the connection is unhealthy, it is closed instead.

        Args:
            smtp: The SMTP connection to release.
        """
        smtp_id = id(smtp)

        async with self._lock:
            key = self._connection_keys.pop(smtp_id, None)
            if key is None:
                # Connection not tracked - just close it
                try:
                    await smtp.quit()
                except Exception:
                    pass
                return

            self._active_count[key] = max(0, self._active_count.get(key, 1) - 1)

            # Check if connection is still healthy before returning to pool
            if await self._is_alive(smtp):
                conn = PooledConnection(smtp=smtp, account_key=key)
                if key not in self._idle:
                    self._idle[key] = []
                self._idle[key].append(conn)
            else:
                try:
                    await smtp.quit()
                except Exception:
                    pass

        # Notify waiters that a connection is available
        condition = await self._get_condition(key)
        async with condition:
            condition.notify()

    @asynccontextmanager
    async def connection(
        self,
        host: str,
        port: int,
        user: str | None,
        password: str | None,
        *,
        use_tls: bool,
        timeout: float | None = 30.0,
    ):
        """Context manager for automatic acquire/release.

        Example:
            async with pool.connection(host, port, user, pwd, use_tls=True) as smtp:
                await smtp.send_message(msg)
        """
        smtp = await self.acquire(host, port, user, password, use_tls=use_tls, timeout=timeout)
        try:
            yield smtp
        finally:
            await self.release(smtp)

    async def get_connection(
        self,
        host: str,
        port: int,
        user: str | None,
        password: str | None,
        *,
        use_tls: bool,
    ) -> aiosmtplib.SMTP:
        """Legacy API: Get a connection (acquire without explicit release).

        DEPRECATED: Use acquire()/release() or connection() context manager.

        This method exists for backward compatibility. Connections obtained
        this way should be released manually, or they will be cleaned up
        when the pool is cleaned.

        Args:
            host: SMTP server hostname.
            port: SMTP server port.
            user: Username for auth, or None.
            password: Password for auth, or None.
            use_tls: Whether to use TLS.

        Returns:
            Connected SMTP instance.
        """
        return await self.acquire(host, port, user, password, use_tls=use_tls)

    async def cleanup(self) -> None:
        """Remove expired and unhealthy connections from idle pools.

        Should be called periodically to prevent resource leaks.
        """
        to_close: list[PooledConnection] = []

        async with self._lock:
            for key in list(self._idle.keys()):
                valid: list[PooledConnection] = []
                for conn in self._idle[key]:
                    if conn.age() > self.ttl or not await self._is_alive(conn.smtp):
                        to_close.append(conn)
                    else:
                        valid.append(conn)
                self._idle[key] = valid

                # Clean up empty entries
                if not self._idle[key]:
                    del self._idle[key]

        # Close connections outside lock
        for conn in to_close:
            await self._close_connection(conn)

    async def close_all(self) -> None:
        """Close all connections in the pool.

        Use when shutting down the application.
        """
        to_close: list[PooledConnection] = []

        async with self._lock:
            for key in list(self._idle.keys()):
                to_close.extend(self._idle[key])
            self._idle.clear()
            self._active_count.clear()
            self._connection_keys.clear()

        for conn in to_close:
            await self._close_connection(conn)

    def stats(self) -> dict:
        """Return pool statistics.

        Returns:
            Dict with idle counts, active counts per account.
        """
        return {
            "idle": {k: len(v) for k, v in self._idle.items()},
            "active": dict(self._active_count),
            "max_per_account": self.max_per_account,
            "ttl": self.ttl,
        }


__all__ = ["SMTPPool", "PooledConnection"]
