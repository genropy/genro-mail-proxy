"""Fetch attachments from genro-storage volumes.

This module provides the StorageFetcher class, a concrete implementation
of the AttachmentFetcherBase interface that uses the genro-storage library
to retrieve files from configured storage volumes.

The fetcher supports any storage backend that genro-storage supports,
including S3, GCS, Azure Blob, local filesystem, HTTP, and WebDAV,
all through a unified volume:path addressing scheme.

Note:
    This module requires genro-storage to be installed. It is only imported
    when genro-storage is available (checked in attachments/__init__.py).

Example:
    Using the StorageFetcher directly::

        from genro_storage import AsyncStorageManager

        storage = AsyncStorageManager()
        storage.configure([
            {"name": "docs", "protocol": "s3", "bucket": "my-docs"}
        ])

        fetcher = StorageFetcher(storage)
        content = await fetcher.fetch({
            "storage_path": "docs:reports/quarterly.pdf"
        })
"""

from typing import TYPE_CHECKING, Any, Dict, Optional

from genro_storage import AsyncStorageManager

from .base import AttachmentFetcherBase

if TYPE_CHECKING:
    from genro_storage import AsyncStorageManager as AsyncStorageManagerType


class StorageFetcher(AttachmentFetcherBase):
    """Attachment fetcher using genro-storage unified interface.

    Implements the AttachmentFetcherBase protocol to retrieve file content
    from genro-storage volumes. Supports all storage backends available in
    the genro-storage library through the configured AsyncStorageManager.

    Attributes:
        _storage: The AsyncStorageManager instance for storage operations.
    """

    def __init__(self, storage_manager: AsyncStorageManager):
        """Initialize the fetcher with a storage manager.

        Args:
            storage_manager: Configured AsyncStorageManager instance with
                mounted volumes. The manager should have volumes configured
                before the fetcher is used.
        """
        self._storage = storage_manager

    async def fetch(self, att: Dict[str, Any]) -> Optional[bytes]:
        """Retrieve file content from a genro-storage volume.

        Uses the storage_path from the attachment dictionary to locate and
        read the file content. The path format is "volume:path/to/file"
        where "volume" is a configured storage volume name.

        Args:
            att: Attachment specification dictionary containing:
                - storage_path: File location in "volume:path" format

        Returns:
            Binary content of the file, or None if storage_path is not
            specified in the attachment dictionary.

        Raises:
            StorageNotFoundError: If the specified volume is not configured
                or the file does not exist at the given path.
            StorageError: If a storage operation fails due to permissions,
                network issues, or other backend-specific errors.
        """
        storage_path = att.get("storage_path")
        if not storage_path:
            return None

        node = self._storage.node(storage_path)
        # Use async read with binary mode
        return await node.read(mode='rb')
