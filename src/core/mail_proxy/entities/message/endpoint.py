# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Message endpoint: CRUD operations for messages.

Designed for introspection by api_base/cli_base to auto-generate routes/commands.
Schema is derived from method signatures via inspect + pydantic.create_model.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal, TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from ...interface.endpoint_base import BaseEndpoint, POST

if TYPE_CHECKING:
    from .table import MessagesTable


# Helper enums and models

class FetchMode(str, Enum):
    """Source mode for fetching email attachments.

    - ENDPOINT: Fetch from configured HTTP endpoint with path parameter.
    - HTTP_URL: Fetch directly from a full HTTP/HTTPS URL.
    - BASE64: Inline base64-encoded content.
    - FILESYSTEM: Fetch from local filesystem path.
    """
    ENDPOINT = "endpoint"
    HTTP_URL = "http_url"
    BASE64 = "base64"
    FILESYSTEM = "filesystem"


class MessageStatus(str, Enum):
    """Current delivery status of an email message.

    - PENDING: Queued and waiting for delivery attempt.
    - DEFERRED: Temporarily delayed (rate limit or soft error).
    - SENT: Successfully delivered to SMTP server.
    - ERROR: Delivery failed with permanent error.
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
            - endpoint: query params (e.g., ``doc_id=123``)
            - http_url: full URL (e.g., ``https://files.myserver.local/file.pdf``)
            - base64: base64-encoded content (or ``base64:`` prefixed)
            - filesystem: absolute path (e.g., ``/var/attachments/file.pdf``)
        mime_type: Optional MIME type override.
        fetch_mode: Explicit fetch mode (endpoint, http_url, base64, filesystem).
            If not provided, inferred from storage_path format.
        content_md5: MD5 hash for cache lookup.
        auth: Optional authentication override for HTTP requests.
    """
    model_config = ConfigDict(extra="forbid")

    filename: Annotated[str, Field(min_length=1, max_length=255, description="Attachment filename")]
    storage_path: Annotated[str, Field(min_length=1, description="Storage path")]
    mime_type: Annotated[str | None, Field(default=None, description="MIME type override")]
    fetch_mode: Annotated[FetchMode | None, Field(default=None, description="Fetch mode")]
    content_md5: Annotated[str | None, Field(default=None, pattern=r"^[a-fA-F0-9]{32}$", description="MD5 hash")]
    auth: Annotated[dict[str, Any] | None, Field(default=None, description="Auth override")]


class MessageEndpoint(BaseEndpoint):
    """Message management endpoint. Methods are introspected for API/CLI generation."""

    name = "messages"

    def __init__(self, table: MessagesTable):
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
        """Add a new message to the queue.

        Args:
            id: Unique message identifier.
            tenant_id: Tenant identifier.
            account_id: SMTP account to use for sending.
            from_addr: Sender email address.
            to: List of recipient addresses.
            subject: Email subject.
            body: Email body content.
            cc: List of CC addresses.
            bcc: List of BCC addresses.
            reply_to: Reply-To address.
            return_path: Return-Path (envelope sender) address.
            content_type: Body content type (plain or html).
            message_id: Custom Message-ID header.
            priority: Priority (0=immediate, 1=high, 2=medium, 3=low).
            deferred_ts: Unix timestamp to defer delivery until.
            batch_code: Batch/campaign identifier for grouping.
            attachments: List of attachment specifications.
            headers: Additional email headers.

        Returns:
            Dict with message info including id and pk.
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
        """Get a single message by ID.

        Args:
            message_id: Message identifier.
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
        """List messages, optionally filtered.

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
            message_pk: Internal message pk (UUID).

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
        """Add multiple messages to the queue in a single operation.

        Args:
            messages: List of message dicts. Each must have:
                - id: Unique message identifier
                - tenant_id: Tenant identifier
                - account_id: SMTP account to use
                - from (or from_addr): Sender address
                - to: Recipient(s)
                - subject: Email subject
                - body: Email body
                Optional: cc, bcc, reply_to, return_path, content_type,
                message_id, priority, deferred_ts, batch_code, attachments, headers.
            default_priority: Default priority for messages without explicit priority.

        Returns:
            Dict with ok=True, queued count, and list of rejected messages.
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

            # Build payload
            payload: dict[str, Any] = {
                "from": from_addr,
                "to": to if isinstance(to, list) else [to],
                "subject": subject,
                "body": msg.get("body", ""),
                "content_type": msg.get("content_type", "plain"),
            }
            for field in ("cc", "bcc", "reply_to", "return_path", "message_id", "attachments", "headers"):
                if msg.get(field):
                    payload[field] = msg[field]

            priority = msg.get("priority")
            if priority is None and default_priority is not None:
                priority = default_priority
            if priority is None:
                priority = 2

            entries.append({
                "id": msg_id,
                "tenant_id": tenant_id,
                "account_id": account_id,
                "priority": priority,
                "deferred_ts": msg.get("deferred_ts"),
                "batch_code": msg.get("batch_code"),
                "payload": payload,
            })

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

        Args:
            tenant_id: Tenant identifier (for authorization check).
            ids: List of message IDs to delete.

        Returns:
            Dict with ok=True, removed count, not_found list, unauthorized list.
        """
        removed = 0
        not_found: list[str] = []
        unauthorized: list[str] = []

        # Get messages that belong to this tenant
        tenant_ids = await self.table.get_ids_for_tenant(ids, tenant_id)

        for msg_id in ids:
            if msg_id not in tenant_ids:
                # Check if message exists at all
                existing = await self.table.existing_ids([msg_id])
                if msg_id in existing:
                    unauthorized.append(msg_id)
                else:
                    not_found.append(msg_id)
                continue

            # Get message pk and delete
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
        """Add computed status field to message dict."""
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
