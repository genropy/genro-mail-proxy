import base64
import sys
import types
import pytest

class _DummySMTP:
    async def connect(self):
        return None

    async def login(self, *_, **__):
        return None

    async def send_message(self, *_, **__):
        return None

    async def noop(self):
        return 250, b"OK"

    async def quit(self):
        return None

sys.modules.setdefault("aiosmtplib", types.SimpleNamespace(SMTP=_DummySMTP))
sys.modules.setdefault("aioboto3", types.SimpleNamespace(Session=lambda: None))
sys.modules.setdefault("aiohttp", types.SimpleNamespace(ClientSession=lambda: None))

from async_mail_service.attachments import AttachmentManager

@pytest.mark.asyncio
async def test_inline_attachment():
    mgr = AttachmentManager()
    data = await mgr.fetch({"filename":"a.txt","content": base64.b64encode(b"hi").decode()})
    assert data == b"hi"

@pytest.mark.asyncio
async def test_url_attachment(monkeypatch):
    expected = b"url-bytes"
    requested_url = "https://example.com/file.bin"

    class DummyResponse:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def read(self):
            return expected

    class DummySession:
        def __init__(self):
            self.requested = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def get(self, url):
            self.requested = url
            return DummyResponse()

    dummy_session = DummySession()
    monkeypatch.setattr("async_mail_service.attachments.url_fetcher.aiohttp.ClientSession", lambda: dummy_session)

    mgr = AttachmentManager()
    data = await mgr.fetch({"filename": "file.bin", "url": requested_url})
    assert data == expected
    assert dummy_session.requested == requested_url

@pytest.mark.asyncio
async def test_s3_attachment(monkeypatch):
    expected = b"s3-bytes"
    bucket = "bucket"
    key = "path/to/file"

    class DummyS3Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get_object(self, Bucket, Key):
            assert Bucket == bucket
            assert Key == key
            class Body:
                async def read(inner_self):
                    return expected
            return {"Body": Body()}

    class DummySession:
        def client(self, name):
            assert name == "s3"
            return DummyS3Client()

    monkeypatch.setattr("async_mail_service.attachments.s3_fetcher.aioboto3.Session", lambda: DummySession())

    mgr = AttachmentManager()
    data = await mgr.fetch({"filename": "doc.pdf", "s3": {"bucket": bucket, "key": key}})
    assert data == expected

@pytest.mark.asyncio
async def test_fetch_returns_none_for_unknown(monkeypatch):
    mgr = AttachmentManager()
    assert await mgr.fetch({"filename": "file.bin"}) is None

def test_guess_mime_known():
    maintype, subtype = AttachmentManager.guess_mime("report.pdf")
    assert maintype == "application"
    assert subtype == "pdf"

def test_guess_mime_unknown():
    maintype, subtype = AttachmentManager.guess_mime("file.unknownext")
    assert maintype == "application"
    assert subtype == "octet-stream"
