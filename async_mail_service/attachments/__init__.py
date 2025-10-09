from typing import Dict, Any, Optional, Tuple
import mimetypes
from .s3_fetcher import S3AttachmentFetcher
from .url_fetcher import URLAttachmentFetcher
from .inline_fetcher import InlineAttachmentFetcher

class AttachmentManager:
    def __init__(self):
        self._s3 = S3AttachmentFetcher()
        self._url = URLAttachmentFetcher()
        self._inline = InlineAttachmentFetcher()

    async def fetch(self, att: Dict[str, Any]) -> Optional[bytes]:
        if "s3" in att:
            return await self._s3.fetch(att)
        if "url" in att:
            return await self._url.fetch(att)
        if "content" in att:
            return await self._inline.fetch(att)
        return None

    @staticmethod
    def guess_mime(filename: str) -> Tuple[str, str]:
        mt, _ = mimetypes.guess_type(filename)
        if not mt:
            return ("application", "octet-stream")
        return tuple(mt.split("/", 1))  # type: ignore[return-value]
