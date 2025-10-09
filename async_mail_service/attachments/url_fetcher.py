"""Download attachments exposed through HTTP(S) URLs."""

from typing import Dict, Any, Optional
import aiohttp
from .base import AttachmentFetcherBase

class URLAttachmentFetcher(AttachmentFetcherBase):
    async def fetch(self, att: Dict[str, Any]) -> Optional[bytes]:
        """Download the attachment referenced by ``url``."""
        url = att.get("url")
        if not url:
            return None
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                return await resp.read()
