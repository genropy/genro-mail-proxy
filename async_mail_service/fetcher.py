from typing import List, Dict, Any, Optional, Awaitable, Callable

import aiohttp

JsonDict = Dict[str, Any]
FetchCallable = Callable[[], Awaitable[List[JsonDict]]]
ReportCallable = Callable[[JsonDict], Awaitable[None]]


class Fetcher:
    def __init__(
        self,
        fetch_url: Optional[str] = None,
        fetch_callable: Optional[FetchCallable] = None,
        report_callable: Optional[ReportCallable] = None,
    ):
        self.fetch_url = fetch_url
        self.fetch_callable = fetch_callable
        self.report_callable = report_callable

    def _endpoint(self, suffix: str) -> Optional[str]:
        if not self.fetch_url:
            return None
        base = self.fetch_url.rstrip("/")
        return f"{base}/{suffix.lstrip('/')}"

    async def fetch_messages(self) -> List[JsonDict]:
        if self.fetch_callable is not None:
            return await self.fetch_callable()
        endpoint = self._endpoint("fetch-messages")
        if not endpoint:
            return []
        async with aiohttp.ClientSession() as session:
            async with session.get(endpoint) as resp:
                resp.raise_for_status()
                data = await resp.json()
                msgs = data.get("messages", [])
                return msgs if isinstance(msgs, list) else []

    async def report_delivery(self, payload: JsonDict) -> None:
        if self.report_callable is not None:
            await self.report_callable(payload)
            return
        endpoint = self._endpoint("delivery-report")
        if not endpoint:
            return
        async with aiohttp.ClientSession() as session:
            async with session.post(endpoint, json=payload) as resp:
                resp.raise_for_status()
