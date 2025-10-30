"""Attachment management using genro-storage."""

from typing import Dict, Any, Optional, Tuple
import mimetypes
from genro_storage import AsyncStorageManager
from .storage_fetcher import StorageFetcher


class AttachmentManager:
    """Manage attachment fetching using genro-storage volumes."""

    def __init__(self, storage_manager: AsyncStorageManager):
        """Initialize with a configured AsyncStorageManager.

        Args:
            storage_manager: Configured AsyncStorageManager with mounted volumes
        """
        self._fetcher = StorageFetcher(storage_manager)

    async def fetch(self, att: Dict[str, Any]) -> Optional[bytes]:
        """Fetch attachment from storage.

        Args:
            att: Attachment dictionary with 'storage_path' key in format 'volume:path/to/file'

        Returns:
            File content as bytes, or None if storage_path is missing
        """
        return await self._fetcher.fetch(att)

    @staticmethod
    def guess_mime(filename: str) -> Tuple[str, str]:
        """Guess the MIME type for the given filename."""
        mt, _ = mimetypes.guess_type(filename)
        if not mt:
            return ("application", "octet-stream")
        return tuple(mt.split("/", 1))  # type: ignore[return-value]
