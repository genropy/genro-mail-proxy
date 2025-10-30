"""Fetch attachments from genro-storage volumes."""

from typing import Dict, Any, Optional
from genro_storage import AsyncStorageManager
from .base import AttachmentFetcherBase


class StorageFetcher(AttachmentFetcherBase):
    """Fetch attachments using genro-storage unified interface."""

    def __init__(self, storage_manager: AsyncStorageManager):
        """Initialize with a configured AsyncStorageManager instance.

        Args:
            storage_manager: Configured AsyncStorageManager with mounted volumes
        """
        self._storage = storage_manager

    async def fetch(self, att: Dict[str, Any]) -> Optional[bytes]:
        """Fetch attachment from storage using volume:path format.

        Args:
            att: Attachment dictionary with 'storage_path' key in format 'volume:path/to/file'

        Returns:
            File content as bytes, or None if storage_path is missing

        Raises:
            StorageNotFoundError: If volume or file doesn't exist
            StorageError: On other storage errors
        """
        storage_path = att.get("storage_path")
        if not storage_path:
            return None

        node = self._storage.node(storage_path)
        # Use async read with binary mode
        return await node.read(mode='rb')
