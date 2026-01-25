import pytest

from mail_proxy.fetcher import Fetcher


@pytest.mark.asyncio
async def test_fetcher_uses_callable():
    async def fake_fetch():
        return [{"id": "1"}]

    fetcher = Fetcher(fetch_callable=fake_fetch)
    assert await fetcher.fetch_messages() == [{"id": "1"}]


@pytest.mark.asyncio
async def test_fetcher_returns_empty_without_source():
    fetcher = Fetcher()
    assert await fetcher.fetch_messages() == []


@pytest.mark.asyncio
async def test_fetcher_fetches_from_http(monkeypatch):
    class DummyResponse:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def json(self):
            return {"messages": [{"id": "from-http"}]}

        def raise_for_status(self):
            return None

    class DummySession:
        def __init__(self):
            self.requested = None
            self.posted = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def get(self, url):
            self.requested = url
            return DummyResponse()

        def post(self, url, json):
            self.posted = (url, json)
            return DummyResponse()

    session = DummySession()
    monkeypatch.setattr("mail_proxy.fetcher.aiohttp.ClientSession", lambda: session)

    fetcher = Fetcher(fetch_url="https://example.com/mail-service-endpoint")
    messages = await fetcher.fetch_messages()
    assert session.requested == "https://example.com/mail-service-endpoint/fetch-messages"
    assert messages == [{"id": "from-http"}]

    await fetcher.report_delivery({"id": "1", "status": "sent"})
    assert session.posted[0] == "https://example.com/mail-service-endpoint/delivery-report"
    assert session.posted[1]["status"] == "sent"


@pytest.mark.asyncio
async def test_fetcher_uses_report_callable():
    events = []

    async def fake_report(payload):
        events.append(payload)

    fetcher = Fetcher(report_callable=fake_report)
    await fetcher.report_delivery({"id": "1", "status": "deferred"})
    assert events == [{"id": "1", "status": "deferred"}]
