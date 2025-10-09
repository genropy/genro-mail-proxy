from typing import Dict, Any, Optional

class AttachmentFetcherBase:
    async def fetch(self, att: Dict[str, Any]) -> Optional[bytes]:
        raise NotImplementedError
