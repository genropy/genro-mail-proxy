import time
from typing import Optional, Dict, Any
from .persistence import Persistence

class RateLimiter:
    def __init__(self, persistence: Persistence):
        self.persistence = persistence

    async def check_and_plan(self, account: Dict[str, Any]) -> Optional[int]:
        account_id = account["id"]
        now = int(time.time())

        def lim(key: str) -> Optional[int]:
            v = account.get(key)
            if v is None:
                return None
            return int(v) if int(v) > 0 else None

        per_min = lim("limit_per_minute")
        per_hour = lim("limit_per_hour")
        per_day = lim("limit_per_day")

        if per_min is not None:
            c = await self.persistence.count_sends_since(account_id, now - 60)
            if c >= per_min:
                return (now // 60 + 1) * 60
        if per_hour is not None:
            c = await self.persistence.count_sends_since(account_id, now - 3600)
            if c >= per_hour:
                return (now // 3600 + 1) * 3600
        if per_day is not None:
            c = await self.persistence.count_sends_since(account_id, now - 86400)
            if c >= per_day:
                return (now // 86400 + 1) * 86400
        return None

    async def log_send(self, account_id: str) -> None:
        await self.persistence.log_send(account_id, int(time.time()))
