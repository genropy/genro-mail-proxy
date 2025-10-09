"""Base protocol for attachment fetchers."""

from typing import Dict, Any, Optional

class AttachmentFetcherBase:
    """Interface implemented by concrete attachment fetchers."""

    async def fetch(self, att: Dict[str, Any]) -> Optional[bytes]:
        """Return the attachment payload or ``None`` when not available."""
        raise NotImplementedError
