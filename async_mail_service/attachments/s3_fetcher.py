from typing import Dict, Any, Optional
import aioboto3
from .base import AttachmentFetcherBase

class S3AttachmentFetcher(AttachmentFetcherBase):
    async def fetch(self, att: Dict[str, Any]) -> Optional[bytes]:
        info = att.get("s3")
        if not info:
            return None
        bucket = info["bucket"]
        key = info["key"]
        async with aioboto3.Session().client("s3") as s3:
            resp = await s3.get_object(Bucket=bucket, Key=key)
            return await resp["Body"].read()
