# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Message entity: email queue entries."""

from .endpoint import (
    AttachmentPayload,
    FetchMode,
    MessageEndpoint,
    MessageStatus,
)
from .table import MessagesTable

__all__ = [
    "AttachmentPayload",
    "FetchMode",
    "MessageEndpoint",
    "MessagesTable",
    "MessageStatus",
]
