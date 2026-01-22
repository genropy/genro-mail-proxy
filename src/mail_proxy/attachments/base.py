# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Base protocol for attachment fetchers.

This module defines the abstract interface that all attachment fetcher
implementations must follow. It provides a consistent API for retrieving
attachment content from various storage backends.

The protocol pattern allows the AttachmentManager to work with different
fetcher implementations interchangeably, supporting extensibility for
new storage backends.
"""

from typing import Any


class AttachmentFetcherBase:
    """Abstract base class defining the attachment fetcher interface.

    All concrete fetcher implementations must inherit from this class
    and implement the fetch method. This ensures consistent behavior
    across different storage backends.
    """

    async def fetch(self, att: dict[str, Any]) -> bytes | None:
        """Retrieve attachment content from storage.

        This method must be implemented by subclasses to provide the
        actual storage retrieval logic.

        Args:
            att: Attachment specification dictionary. The exact keys
                required depend on the specific fetcher implementation,
                but typically includes 'storage_path' or similar
                location identifier.

        Returns:
            Binary content of the attachment if found and accessible,
            None if the attachment cannot be located or the required
            path information is missing.

        Raises:
            NotImplementedError: If called on the base class directly.
        """
        raise NotImplementedError
