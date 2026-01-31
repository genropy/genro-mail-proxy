# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Storage abstraction layer compatible with genro-storage API.

This module provides a simplified storage API that mirrors genro-storage.
When genro-storage is ready, simply replace the import.

CE (Core Edition): Local filesystem storage only.
EE (Enterprise): Adds cloud storage backends (S3, Azure, GCS) via fsspec.

Class Composition:
    StorageNode is dynamically composed with StorageNode_EE mixin when
    the enterprise package is installed, following the same pattern used
    for Table classes in proxy_base.py.

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

from .node import StorageNode as _StorageNodeCE

# Compose StorageNode with EE mixin if available
try:
    from enterprise.mail_proxy.storage.node_ee import StorageNode_EE

    StorageNode = type(
        "StorageNode", (StorageNode_EE, _StorageNodeCE), {"__module__": _StorageNodeCE.__module__}
    )
except ImportError:
    StorageNode = _StorageNodeCE

from .manager import StorageManager

__all__ = ["StorageManager", "StorageNode"]
