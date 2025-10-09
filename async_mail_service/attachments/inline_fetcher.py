from typing import Dict, Any, Optional
import base64
from .base import AttachmentFetcherBase

class InlineAttachmentFetcher(AttachmentFetcherBase):
    async def fetch(self, att: Dict[str, Any]) -> Optional[bytes]:
        content = att.get("content")
        if not content:
            return None
        return base64.b64decode(content)
