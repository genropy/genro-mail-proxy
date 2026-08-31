# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Base64 inline content decoder.

This module provides a fetcher for base64-encoded attachment content
that is embedded directly in the storage_path.

Example:
    Decoding an inline attachment::

        fetcher = Base64Fetcher()
        content = await fetcher.fetch("SGVsbG8gV29ybGQh")
        # content == b"Hello World!"
"""

from __future__ import annotations

import base64


class Base64Fetcher:
    """Decoder for base64-encoded inline attachment content.

    Handles storage paths in the format "base64:ENCODED_CONTENT"
    where ENCODED_CONTENT is standard base64-encoded binary data.
    """

    async def fetch(self, base64_content: str) -> bytes | None:
        """Decode base64 content to bytes.

        Args:
            base64_content: The base64-encoded string (without the "base64:" prefix).

        Returns:
            Decoded binary content, or None if input is empty.

        Raises:
            ValueError: If the base64 content is invalid.
        """
        if not base64_content:
            return None

        try:
            # Handle missing padding
            content = base64_content.strip()
            # Add padding if necessary
            padding_needed = 4 - (len(content) % 4)
            if padding_needed != 4:
                content += "=" * padding_needed

            return base64.b64decode(content, validate=True)
        except Exception as e:
            raise ValueError(f"Invalid base64 content: {e}") from e
