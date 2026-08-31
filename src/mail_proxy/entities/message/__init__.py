# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Message entity: email queue entries."""

from .schema import (
    AttachmentPayload,
    FetchMode,
    Message,
    MessageCreate,
    MessageListItem,
    MessageStatus,
)
from .table import MessagesTable

__all__ = [
    "AttachmentPayload",
    "FetchMode",
    "Message",
    "MessageCreate",
    "MessageListItem",
    "MessagesTable",
    "MessageStatus",
]
