# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Storage abstraction layer compatible with genro-storage API.

This module provides a simplified storage API that mirrors genro-storage.
When genro-storage is ready, simply replace the import.

CE (Core Edition): Local filesystem storage only.
EE (Enterprise): Adds cloud storage backends (S3, Azure, GCS) via fsspec.

Usage:
    from storage import StorageManager

    storage = StorageManager()
    storage.configure([
        {'name': 'attachments', 'protocol': 'local', 'base_path': '/data/attachments'},
    ])

    # Write a file
    node = storage.node('attachments:files/report.pdf')
    await node.write_bytes(content)

    # Read a file
    data = await node.read_bytes()

    # Get download URL (if supported)
    url = node.url(expires_in=3600)
"""

from .manager import StorageManager
from .node import StorageNode

__all__ = ["StorageManager", "StorageNode"]
