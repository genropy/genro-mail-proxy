# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: BSL-1.1
"""Large file storage for oversized email attachments.

This package provides multi-cloud storage for attachments that exceed
the configured size threshold, using fsspec for backend abstraction.

Components:
    LargeFileStorage: Upload/download manager for external file storage.
    LargeFileStorageError: Exception for storage operation failures.

Example:
    Store large attachments in S3::

        from enterprise.mail_proxy.attachments import LargeFileStorage

        storage = LargeFileStorage(
            storage_url="s3://my-bucket/mail-attachments",
        )
        await storage.upload("file-123", content, "report.pdf")
        url = storage.get_download_url("file-123", "report.pdf")

    Use local filesystem with public URL::

        storage = LargeFileStorage(
            storage_url="file:///var/www/downloads",
            public_base_url="https://files.example.com",
        )

Note:
    Supported backends include S3, GCS, Azure Blob, and local filesystem.
    Download URLs can be signed with HMAC tokens for secure access.
"""

from .large_file_storage import LargeFileStorage

__all__ = ["LargeFileStorage"]
