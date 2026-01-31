# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Email message REST API endpoint.

This module provides the MessageEndpoint class exposing CRUD operations
for email messages via REST API and CLI commands.

The endpoint is designed for automatic introspection by api_base and
cli_base modules, which generate FastAPI routes and Typer commands
from method signatures.

Example:
    CLI commands auto-generated::

        mail-proxy messages add --tenant-id acme --id msg-001 --account-id main ...
        mail-proxy messages list --tenant-id acme
        mail-proxy messages get --message-id msg-001 --tenant-id acme
        mail-proxy messages delete --message-pk uuid-...
        mail-proxy messages add-batch --messages '[{...}, {...}]'
        mail-proxy messages cleanup --tenant-id acme

Note:
    Enterprise Edition (EE) extends this with MessageEndpoint_EE mixin
    adding PEC-specific status information.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ...interface.endpoint_base import POST, BaseEndpoint

if TYPE_CHECKING:
    from .table import MessagesTable


class FetchMode(str, Enum):
    """Source mode for fetching email attachments.

    Attributes:
        ENDPOINT: Fetch from configured HTTP endpoint with path parameter.
        HTTP_URL: Fetch directly from a full HTTP/HTTPS URL.
        BASE64: Inline base64-encoded content.
        FILESYSTEM: Fetch from local filesystem path.
    """

    ENDPOINT = "endpoint"
    HTTP_URL = "http_url"
    BASE64 = "base64"
    FILESYSTEM = "filesystem"


class MessageStatus(str, Enum):
    """Current delivery status of an email message.

    Attributes:
        PENDING: Queued and waiting for delivery attempt.
        DEFERRED: Temporarily delayed (rate limit or soft error).
        SENT: Successfully delivered to SMTP server.
        ERROR: Delivery failed with permanent error.
    """

    PENDING = "pending"
    DEFERRED = "deferred"
    SENT = "sent"
    ERROR = "error"


class AttachmentPayload(BaseModel):
    """Email attachment specification.

    Attributes:
        filename: Attachment filename (may contain MD5 marker).
        storage_path: Content location. Format depends on fetch_mode:
            - endpoint: query params (e.g., "doc_id=123")
            - http_url: full URL (e.g., "https://files.example.com/file.pdf")
            - base64: base64-encoded content
            - filesystem: absolute path (e.g., "/var/attachments/file.pdf")
        mime_type: Optional MIME type override.
        fetch_mode: Explicit fetch mode. If not provided, inferred from path.
        content_md5: MD5 hash for cache lookup.
        auth: Optional authentication override for HTTP requests.

    Example:
        Attachment from HTTP URL::

            AttachmentPayload(
                filename="report.pdf",
                storage_path="https://cdn.example.com/docs/report.pdf",
                fetch_mode=FetchMode.HTTP_URL,
            )
    """

    model_config = ConfigDict(extra="forbid")

    filename: Annotated[str, Field(min_length=1, max_length=255, description="Attachment filename")]
    storage_path: Annotated[str, Field(min_length=1, description="Storage path")]
    mime_type: Annotated[str | None, Field(default=None, description="MIME type override")]
    fetch_mode: Annotated[FetchMode | None, Field(default=None, description="Fetch mode")]
    content_md5: Annotated[
        str | None, Field(default=None, pattern=r"^[a-fA-F0-9]{32}$", description="MD5 hash")
    ]
    auth: Annotated[dict[str, Any] | None, Field(default=None, description="Auth override")]


class MessageEndpoint(BaseEndpoint):
    """REST API endpoint for email message management.

    Provides CRUD operations for email messages including batch
    operations for bulk insert/delete and cleanup of old messages.

    Attributes:
        name: Endpoint name used in URL paths ("messages").
        table: MessagesTable instance for database operations.

    Example:
        Using the endpoint programmatically::

            endpoint = MessageEndpoint(db.table("messages"))

            # Add a single message
            msg = await endpoint.add(
                id="msg-001",
                tenant_id="acme",
                account_id="main",
                from_addr="sender@acme.com",
                to=["user@example.com"],
                subject="Test",
                body="Hello",
            )

            # List messages
            messages = await endpoint.list(tenant_id="acme")
    """

    name = "messages"

    def __init__(self, table: MessagesTable):
        """Initialize endpoint with table reference.

        Args:
            table: MessagesTable instance for database operations.
        """
        super().__init__(table)

    @POST
    async def add(
        self,
        id: str,
        tenant_id: str,
        account_id: str,
        from_addr: str,
        to: list[str],
        subject: str,
        body: str,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        reply_to: str | None = None,
        return_path: str | None = None,
        content_type: Literal["plain", "html"] = "plain",
        message_id: str | None = None,
        priority: int = 2,
        deferred_ts: int | None = None,
        batch_code: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict:
        """Add a new message to the delivery queue.

        Args:
            id: Unique message identifier (client-provided).
            tenant_id: Tenant identifier.
            account_id: SMTP account to use for sending.
            from_addr: Sender email address.
            to: List of recipient addresses.
            subject: Email subject line.
            body: Email body content.
            cc: List of CC addresses.
            bcc: List of BCC addresses.
            reply_to: Reply-To address.
            return_path: Return-Path (envelope sender) address.
            content_type: Body content type ("plain" or "html").
            message_id: Custom Message-ID header.
            priority: Delivery priority (0=immediate, 1=high, 2=medium, 3=low).
            deferred_ts: Unix timestamp to defer delivery until.
            batch_code: Batch/campaign identifier for grouping.
            attachments: List of attachment specifications.
            headers: Additional email headers.

        Returns:
            Dict with message id and pk.

        Raises:
            ValueError: If message could not be added.
        """
        payload = {
            "from": from_addr,
            "to": to,
            "subject": subject,
            "body": body,
            "content_type": content_type,
        }
        if cc:
            payload["cc"] = cc
        if bcc:
            payload["bcc"] = bcc
        if reply_to:
            payload["reply_to"] = reply_to
        if return_path:
            payload["return_path"] = return_path
        if message_id:
            payload["message_id"] = message_id
        if attachments:
            payload["attachments"] = attachments
        if headers:
            payload["headers"] = headers

        entry = {
            "id": id,
            "tenant_id": tenant_id,
            "account_id": account_id,
            "priority": priority,
            "deferred_ts": deferred_ts,
            "batch_code": batch_code,
            "payload": payload,
        }

        result = await self.table.insert_batch([entry], tenant_id=tenant_id)
        if result:
            return result[0]
        raise ValueError(f"Failed to add message '{id}'")

    async def get(self, message_id: str, tenant_id: str) -> dict:
        """Retrieve a single message by ID.

        Args:
            message_id: Client-provided message identifier.
            tenant_id: Tenant identifier.

        Returns:
            Message dict with status and payload.

        Raises:
            ValueError: If message not found.
        """
        message = await self.table.get(message_id, tenant_id)
        if not message:
            raise ValueError(f"Message '{message_id}' not found")
        return self._add_status(message)

    async def list(
        self,
        tenant_id: str | None = None,
        active_only: bool = False,
        include_history: bool = False,
    ) -> list[dict]:
        """List messages with optional filters.

        Args:
            tenant_id: Filter by tenant.
            active_only: Only return pending messages.
            include_history: Include event history for each message.

        Returns:
            List of message dicts with status info.
        """
        messages = await self.table.list_all(
            tenant_id=tenant_id,
            active_only=active_only,
            include_history=include_history,
        )
        return [self._add_status(m) for m in messages]

    @POST
    async def delete(self, message_pk: str) -> bool:
        """Delete a message by internal primary key.

        Args:
            message_pk: Internal message UUID.

        Returns:
            True if deleted, False if not found.
        """
        return await self.table.remove_by_pk(message_pk)

    async def count_active(self) -> int:
        """Count messages awaiting delivery.

        Returns:
            Number of active (pending) messages.
        """
        return await self.table.count_active()

    async def count_pending_for_tenant(
        self,
        tenant_id: str,
        batch_code: str | None = None,
    ) -> int:
        """Count pending messages for a tenant.

        Args:
            tenant_id: Tenant identifier.
            batch_code: Optional batch code filter.

        Returns:
            Number of pending messages.
        """
        return await self.table.count_pending_for_tenant(tenant_id, batch_code)

    @POST
    async def add_batch(
        self,
        messages: list[dict[str, Any]],
        default_priority: int | None = None,
    ) -> dict:
        """Add multiple messages in a single operation.

        Validates each message and queues valid ones. Invalid messages
        are reported in the rejected list.

        Args:
            messages: List of message dicts. Each must have:
                - id: Unique message identifier
                - tenant_id: Tenant identifier
                - account_id: SMTP account to use
                - from (or from_addr): Sender address
                - to: Recipient(s)
                - subject: Email subject
                Optional: body, cc, bcc, reply_to, return_path, content_type,
                message_id, priority, deferred_ts, batch_code, attachments, headers.
            default_priority: Default priority for messages without explicit priority.

        Returns:
            Dict with:
                - ok: True
                - queued: Number of messages queued
                - rejected: List of {id, reason} for invalid messages

        Example:
            ::

                result = await endpoint.add_batch([
                    {"id": "m1", "tenant_id": "t1", "account_id": "a1",
                     "from": "a@b.com", "to": ["c@d.com"], "subject": "Hi"},
                    {"id": "m2", ...},
                ])
                # Returns: {"ok": True, "queued": 2, "rejected": []}
        """
        queued = 0
        rejected: list[dict[str, str | None]] = []

        entries = []
        for msg in messages:
            msg_id = msg.get("id")
            if not msg_id:
                rejected.append({"id": None, "reason": "Missing 'id' field"})
                continue

            tenant_id = msg.get("tenant_id")
            if not tenant_id:
                rejected.append({"id": msg_id, "reason": "Missing 'tenant_id' field"})
                continue

            account_id = msg.get("account_id")
            if not account_id:
                rejected.append({"id": msg_id, "reason": "Missing 'account_id' field"})
                continue

            from_addr = msg.get("from") or msg.get("from_addr")
            if not from_addr:
                rejected.append({"id": msg_id, "reason": "Missing 'from' field"})
                continue

            to = msg.get("to")
            if not to:
                rejected.append({"id": msg_id, "reason": "Missing 'to' field"})
                continue

            subject = msg.get("subject")
            if not subject:
                rejected.append({"id": msg_id, "reason": "Missing 'subject' field"})
                continue

            payload: dict[str, Any] = {
                "from": from_addr,
                "to": to if isinstance(to, list) else [to],
                "subject": subject,
                "body": msg.get("body", ""),
                "content_type": msg.get("content_type", "plain"),
            }
            for field in (
                "cc",
                "bcc",
                "reply_to",
                "return_path",
                "message_id",
                "attachments",
                "headers",
            ):
                if msg.get(field):
                    payload[field] = msg[field]

            priority = msg.get("priority")
            if priority is None and default_priority is not None:
                priority = default_priority
            if priority is None:
                priority = 2

            entries.append(
                {
                    "id": msg_id,
                    "tenant_id": tenant_id,
                    "account_id": account_id,
                    "priority": priority,
                    "deferred_ts": msg.get("deferred_ts"),
                    "batch_code": msg.get("batch_code"),
                    "payload": payload,
                }
            )

        if entries:
            result = await self.table.insert_batch(entries)
            queued = len(result)

        return {"ok": True, "queued": queued, "rejected": rejected}

    @POST
    async def delete_batch(
        self,
        tenant_id: str,
        ids: list[str],
    ) -> dict:
        """Delete multiple messages by their IDs.

        Validates ownership before deletion.

        Args:
            tenant_id: Tenant identifier for authorization check.
            ids: List of message IDs to delete.

        Returns:
            Dict with:
                - ok: True
                - removed: Number of messages deleted
                - not_found: List of IDs that don't exist
                - unauthorized: List of IDs belonging to other tenants
        """
        removed = 0
        not_found: list[str] = []
        unauthorized: list[str] = []

        tenant_ids = await self.table.get_ids_for_tenant(ids, tenant_id)

        for msg_id in ids:
            if msg_id not in tenant_ids:
                existing = await self.table.existing_ids([msg_id])
                if msg_id in existing:
                    unauthorized.append(msg_id)
                else:
                    not_found.append(msg_id)
                continue

            msg = await self.table.get(msg_id, tenant_id)
            if msg and await self.table.remove_by_pk(msg["pk"]):
                removed += 1
            else:
                not_found.append(msg_id)

        return {
            "ok": True,
            "removed": removed,
            "not_found": not_found if not_found else None,
            "unauthorized": unauthorized if unauthorized else None,
        }

    async def cleanup(
        self,
        tenant_id: str,
        older_than_seconds: int | None = None,
    ) -> dict:
        """Clean up fully reported messages older than retention period.

        Removes messages that have been delivered and reported to the
        client, freeing up database space.

        Args:
            tenant_id: Tenant identifier.
            older_than_seconds: Messages reported before (now - older_than_seconds)
                will be deleted. Defaults to 86400 (24 hours).

        Returns:
            Dict with ok=True and removed count.
        """
        import time

        retention = older_than_seconds if older_than_seconds is not None else 86400
        threshold_ts = int(time.time()) - retention
        removed = await self.table.remove_fully_reported_before_for_tenant(threshold_ts, tenant_id)
        return {"ok": True, "removed": removed}

    def _add_status(self, message: dict) -> dict:
        """Add computed status field to message dict.

        Args:
            message: Message dict from database.

        Returns:
            Message dict with 'status' field added.
        """
        if message.get("smtp_ts") is not None:
            if message.get("error"):
                message["status"] = MessageStatus.ERROR.value
            else:
                message["status"] = MessageStatus.SENT.value
        elif message.get("deferred_ts") is not None:
            message["status"] = MessageStatus.DEFERRED.value
        else:
            message["status"] = MessageStatus.PENDING.value
        return message


__all__ = [
    "AttachmentPayload",
    "FetchMode",
    "MessageEndpoint",
    "MessageStatus",
]
