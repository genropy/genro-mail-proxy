# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Fake SMTP server that simulates various error conditions.

This server is used for integration testing to verify error handling,
retry logic, and SMTP error classification.

Environment variables:
    SMTP_ERROR_MODE: The type of error to simulate
        - "none": Normal operation (accepts all emails)
        - "reject_all": Reject all emails with 550 error
        - "temp_fail": Temporary failure (451) - should trigger retry
        - "auth_fail": Authentication required (530)
        - "timeout": Slow response (simulates timeout)
        - "random": Random mix of success/errors
        - "rate_limit": Accept first N emails, then reject (452)
    SMTP_PORT: Port to listen on (default: 1025)
    SMTP_RATE_LIMIT: Number of emails before rate limiting (default: 5)
    SMTP_TIMEOUT_SECONDS: Delay for timeout mode (default: 30)
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
from email.message import EmailMessage

from aiosmtpd.controller import Controller
from aiosmtpd.smtp import SMTP, Envelope, Session

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class ErrorSimulatingHandler:
    """SMTP handler that simulates various error conditions."""

    def __init__(self):
        self.error_mode = os.environ.get("SMTP_ERROR_MODE", "none")
        self.rate_limit = int(os.environ.get("SMTP_RATE_LIMIT", "5"))
        self.timeout_seconds = int(os.environ.get("SMTP_TIMEOUT_SECONDS", "30"))
        self.message_count = 0
        self.messages: list[dict] = []

        logger.info(f"Error SMTP started with mode: {self.error_mode}")
        if self.error_mode == "rate_limit":
            logger.info(f"Rate limit set to: {self.rate_limit} messages")

    async def handle_RCPT(
        self,
        server: SMTP,
        session: Session,
        envelope: Envelope,
        address: str,
        rcpt_options: list[str],
    ) -> str:
        """Handle RCPT TO command - can reject recipients."""

        # Check for special recipient patterns
        if "@reject." in address:
            logger.info(f"Rejecting recipient: {address}")
            return "550 User not found"

        if "@tempfail." in address:
            logger.info(f"Temp fail for recipient: {address}")
            return "451 Temporary failure, try again later"

        envelope.rcpt_tos.append(address)
        return "250 OK"

    async def handle_DATA(self, server: SMTP, session: Session, envelope: Envelope) -> str:
        """Handle DATA command - main error simulation point."""

        self.message_count += 1
        msg_num = self.message_count

        logger.info(f"Received message #{msg_num} from {envelope.mail_from} to {envelope.rcpt_tos}")

        # Store message for inspection
        self.messages.append({
            "id": msg_num,
            "from": envelope.mail_from,
            "to": envelope.rcpt_tos,
            "data": envelope.content.decode("utf-8", errors="replace")[:500],
        })

        # Apply error mode
        if self.error_mode == "none":
            return "250 Message accepted"

        elif self.error_mode == "reject_all":
            logger.warning(f"Rejecting message #{msg_num} (reject_all mode)")
            return "550 Mailbox not found - permanent failure"

        elif self.error_mode == "temp_fail":
            logger.warning(f"Temp fail for message #{msg_num}")
            return "451 Temporary service failure, please retry"

        elif self.error_mode == "auth_fail":
            logger.warning(f"Auth fail for message #{msg_num}")
            return "530 Authentication required"

        elif self.error_mode == "timeout":
            logger.warning(f"Simulating timeout for message #{msg_num} ({self.timeout_seconds}s)")
            await asyncio.sleep(self.timeout_seconds)
            return "250 Message accepted (after delay)"

        elif self.error_mode == "rate_limit":
            if msg_num <= self.rate_limit:
                logger.info(f"Accepting message #{msg_num} (under rate limit)")
                return "250 Message accepted"
            else:
                logger.warning(f"Rate limited message #{msg_num}")
                return "452 Too many messages, slow down"

        elif self.error_mode == "random":
            # 60% success, 20% temp fail, 10% permanent fail, 10% slow
            roll = random.random()
            if roll < 0.6:
                return "250 Message accepted"
            elif roll < 0.8:
                logger.warning(f"Random temp fail for message #{msg_num}")
                return "451 Temporary failure (random)"
            elif roll < 0.9:
                logger.warning(f"Random permanent fail for message #{msg_num}")
                return "550 Permanent failure (random)"
            else:
                logger.warning(f"Random slow response for message #{msg_num}")
                await asyncio.sleep(5)
                return "250 Message accepted (slow)"

        elif self.error_mode == "pattern":
            # Error based on recipient pattern
            # Already handled in handle_RCPT, accept here
            return "250 Message accepted"

        else:
            logger.warning(f"Unknown error mode: {self.error_mode}, accepting")
            return "250 Message accepted"


class ErrorSMTPController(Controller):
    """Custom controller with configurable settings."""

    def factory(self):
        return SMTP(self.handler, hostname=self.hostname)


def main():
    """Start the error-simulating SMTP server."""
    port = int(os.environ.get("SMTP_PORT", "1025"))
    handler = ErrorSimulatingHandler()

    controller = ErrorSMTPController(
        handler,
        hostname="0.0.0.0",
        port=port,
    )

    logger.info(f"Starting Error SMTP server on port {port}")
    logger.info(f"Error mode: {handler.error_mode}")

    controller.start()

    try:
        # Keep running
        asyncio.get_event_loop().run_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        controller.stop()


if __name__ == "__main__":
    main()
