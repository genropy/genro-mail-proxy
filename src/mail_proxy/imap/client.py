# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Async IMAP client wrapper for bounce detection."""

from __future__ import annotations

import ssl
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from logging import Logger


@dataclass
class IMAPMessage:
    """Represents a fetched IMAP message."""

    uid: int
    raw: bytes


class IMAPClient:
    """Async IMAP client wrapper using aioimaplib."""

    def __init__(self, logger: Logger | None = None):
        self._client: "aioimaplib.IMAP4_SSL | aioimaplib.IMAP4 | None" = None
        self._logger = logger
        self._uidvalidity: int | None = None

    async def connect(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        use_ssl: bool = True,
    ) -> None:
        """Connect and authenticate to IMAP server."""
        import aioimaplib

        if use_ssl:
            ssl_context = ssl.create_default_context()
            self._client = aioimaplib.IMAP4_SSL(host=host, port=port, ssl_context=ssl_context)
        else:
            self._client = aioimaplib.IMAP4(host=host, port=port)

        await self._client.wait_hello_from_server()
        response = await self._client.login(user, password)
        if response.result != "OK":
            raise ConnectionError(f"IMAP login failed: {response.lines}")

        if self._logger:
            self._logger.debug("IMAP connected to %s:%d as %s", host, port, user)

    async def select_folder(self, folder: str = "INBOX") -> int:
        """Select mailbox folder. Returns UIDVALIDITY."""
        if not self._client:
            raise RuntimeError("Not connected")

        response = await self._client.select(folder)
        if response.result != "OK":
            raise RuntimeError(f"Failed to select folder {folder}: {response.lines}")

        # Parse UIDVALIDITY from response
        for line in response.lines:
            if isinstance(line, bytes):
                line = line.decode("utf-8", errors="replace")
            if "UIDVALIDITY" in line:
                # Format: [UIDVALIDITY 123456]
                import re
                match = re.search(r"UIDVALIDITY\s+(\d+)", line)
                if match:
                    self._uidvalidity = int(match.group(1))
                    break

        if self._logger:
            self._logger.debug("Selected folder %s, UIDVALIDITY=%s", folder, self._uidvalidity)

        return self._uidvalidity or 0

    @property
    def uidvalidity(self) -> int | None:
        """Return current UIDVALIDITY value."""
        return self._uidvalidity

    async def fetch_since_uid(self, last_uid: int) -> list[IMAPMessage]:
        """Fetch messages with UID greater than last_uid."""
        if not self._client:
            raise RuntimeError("Not connected")

        messages: list[IMAPMessage] = []

        # Search for UIDs greater than last_uid
        search_criteria = f"UID {last_uid + 1}:*"
        response = await self._client.uid_search(search_criteria)

        if response.result != "OK":
            if self._logger:
                self._logger.warning("IMAP search failed: %s", response.lines)
            return messages

        # Parse UIDs from response
        uids: list[int] = []
        for line in response.lines:
            if isinstance(line, bytes):
                line = line.decode("utf-8", errors="replace")
            if line and line.strip():
                for uid_str in line.split():
                    if uid_str.isdigit():
                        uid = int(uid_str)
                        if uid > last_uid:
                            uids.append(uid)

        if not uids:
            return messages

        if self._logger:
            self._logger.debug("Found %d new messages (UIDs: %s)", len(uids), uids[:10])

        # Fetch each message
        for uid in uids:
            response = await self._client.uid("FETCH", str(uid), "(RFC822)")
            if response.result == "OK":
                # Parse raw message from response
                # aioimaplib returns the RFC822 content as bytearray, not bytes
                for item in response.lines:
                    if isinstance(item, (bytes, bytearray)) and item:
                        # Skip IMAP protocol lines (e.g., "1 FETCH (UID 42 RFC822 {1336}")
                        if isinstance(item, bytes) and item.startswith(b"1 FETCH"):
                            continue
                        raw = bytes(item) if isinstance(item, bytearray) else item
                        messages.append(IMAPMessage(uid=uid, raw=raw))
                        break

        return messages

    async def close(self) -> None:
        """Close IMAP connection."""
        if self._client:
            try:
                await self._client.logout()
            except Exception:
                pass
            self._client = None
            if self._logger:
                self._logger.debug("IMAP connection closed")


__all__ = ["IMAPClient", "IMAPMessage"]
