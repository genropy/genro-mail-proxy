# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: BSL-1.1
"""Cloud storage backends for attachment handling.

This package extends the core storage node with cloud storage support
using fsspec for S3, Google Cloud Storage, and Azure Blob backends.

Components:
    StorageNode_EE: Mixin adding cloud storage filesystem operations.

Example:
    Configure S3 storage for large attachments::

        storage_config = {
            "protocol": "s3",
            "bucket": "my-bucket",
            "path": "mail-attachments",
            "key": "AKIAIOSFODNN7EXAMPLE",
            "secret": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        }
        # StorageNode_EE._get_fs() creates fsspec filesystem

Note:
    Requires `pip install genro-mail-proxy[cloud-storage]` for fsspec
    and appropriate backend dependencies (s3fs, gcsfs, adlfs).
"""

from .node_ee import StorageNode_EE

__all__ = ["StorageNode_EE"]
