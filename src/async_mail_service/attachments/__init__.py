"""Attachment management with optional genro-storage support.

This module provides the AttachmentManager class for retrieving email
attachments from various storage backends. When genro-storage is installed,
it supports S3, GCS, Azure Blob, local filesystem, HTTP, and WebDAV backends.

Attachments are referenced using a volume:path notation that maps to
configured storage volumes, allowing flexible multi-tenant storage
configurations.

The genro-storage dependency is optional. When not installed:
- AttachmentManager can still be instantiated (with storage_manager=None)
- The fetch() method will return None for all attachments
- MIME type detection via guess_mime() remains fully functional

Example:
    Fetching an attachment from storage (with genro-storage installed)::

        from genro_storage import AsyncStorageManager
        from async_mail_service.attachments import AttachmentManager

        storage = AsyncStorageManager()
        storage.configure([
            {"name": "documents", "protocol": "s3", "bucket": "docs"}
        ])

        manager = AttachmentManager(storage)
        content = await manager.fetch({
            "filename": "report.pdf",
            "storage_path": "documents:reports/2025/report.pdf"
        })

    Using without genro-storage (attachment fetching disabled)::

        from async_mail_service.attachments import AttachmentManager

        manager = AttachmentManager(None)
        # fetch() will return None, but guess_mime() works normally
"""

import mimetypes
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

# Optional genro-storage import
try:
    from genro_storage import AsyncStorageManager
    GENRO_STORAGE_AVAILABLE = True
except ImportError:
    AsyncStorageManager = None  # type: ignore[misc, assignment]
    GENRO_STORAGE_AVAILABLE = False

if TYPE_CHECKING:
    from genro_storage import AsyncStorageManager as AsyncStorageManagerType


class AttachmentManager:
    """High-level interface for fetching email attachments from storage.

    Wraps the genro-storage library to provide a simple API for retrieving
    attachment content from configured storage volumes. Handles MIME type
    detection for proper email attachment encoding.

    When genro-storage is not installed, the manager can still be instantiated
    but fetch() will always return None. MIME type detection remains available.

    Attributes:
        _fetcher: Internal StorageFetcher instance for storage operations,
            or None if genro-storage is not available.
    """

    def __init__(self, storage_manager: Optional["AsyncStorageManagerType"]):
        """Initialize the attachment manager with a storage backend.

        Args:
            storage_manager: Configured AsyncStorageManager instance with
                mounted volumes, or None if storage is not available.
                The manager should already have volumes configured
                before being passed to this constructor.
        """
        self._fetcher = None
        if storage_manager is not None and GENRO_STORAGE_AVAILABLE:
            from .storage_fetcher import StorageFetcher
            self._fetcher = StorageFetcher(storage_manager)

    async def fetch(self, att: Dict[str, Any]) -> Optional[bytes]:
        """Retrieve attachment content from storage.

        Fetches the binary content of an attachment using the storage path
        specified in the attachment dictionary. The storage path uses
        volume:path notation to identify both the storage backend and
        the file location.

        Args:
            att: Attachment specification dictionary containing:
                - storage_path: Location in format "volume:path/to/file"
                - filename: Original filename (used for MIME detection)

        Returns:
            Binary content of the attachment, or None if:
            - storage_path is not specified in the attachment dictionary
            - genro-storage is not installed
            - storage_manager was not provided during initialization

        Raises:
            StorageNotFoundError: If the volume or file does not exist
                (only when genro-storage is available).
            StorageError: If a storage operation fails
                (only when genro-storage is available).
        """
        if self._fetcher is None:
            return None
        return await self._fetcher.fetch(att)

    @staticmethod
    def guess_mime(filename: str) -> Tuple[str, str]:
        """Determine the MIME type for a filename based on its extension.

        Uses Python's mimetypes module to detect the appropriate MIME type
        for email attachment encoding. Falls back to application/octet-stream
        for unrecognized extensions.

        This method works independently of genro-storage availability.

        Args:
            filename: Name of the file including extension.

        Returns:
            Tuple of (maintype, subtype) for the MIME type. For example,
            "document.pdf" returns ("application", "pdf").
        """
        mt, _ = mimetypes.guess_type(filename)
        if not mt:
            return ("application", "octet-stream")
        return tuple(mt.split("/", 1))  # type: ignore[return-value]


def is_storage_available() -> bool:
    """Check if genro-storage is installed and available.

    Returns:
        True if genro-storage can be imported, False otherwise.
    """
    return GENRO_STORAGE_AVAILABLE
