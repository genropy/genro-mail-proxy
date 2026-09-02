# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""The open-access guard: no configured token means every request is admin.

Implementation test of ``MailProxyApplication.authenticate_request``. It is the
one v1 auth rule no e2e suite can reach, because every harness and every
deployment starts the proxy WITH a token — so the branch that allows an
unauthenticated request has to be asserted here, on the method itself.

Only that branch: a token that IS presented sends the method to the tenants
table, which needs a real database, and ``tests/unit/10_asgi`` already asserts
those paths against a real server with real keys.

The former companion of this file asserted the other guard, ``service is
None`` answering 500 on every route. That state no longer exists: ``core`` is a
required constructor argument of the application, so an app without an engine
cannot be built. The guard became a construction requirement, and the test that
photographed it went with it (0.7.7 transport swap).
"""

from __future__ import annotations

import pytest

from mail_proxy.auth_tags import ADMIN_IDENTITY, ADMIN_TAG, TENANT_TAG
from mail_proxy.mail_proxy_application import MailProxyApplication

API_TOKEN = "secret-token"


class DummyCore:
    """The smallest engine the application accepts: it is never called here."""

    def __init__(self):
        self._active = True


def _scope(token: str | None = None) -> dict:
    """An http scope carrying the ``X-API-Token`` header, or carrying none."""
    headers = [(b"x-api-token", token.encode())] if token else []
    return {"type": "http", "headers": headers}


def _application(api_token: str | None) -> MailProxyApplication:
    return MailProxyApplication(core=DummyCore(), api_token=api_token)


class TestNoTokenConfigured:
    """An application built without a token treats every caller as admin."""

    @pytest.mark.asyncio
    async def test_a_request_without_a_token_is_admin(self):
        avatar = await _application(None).authenticate_request(_scope())
        assert avatar is not None
        assert avatar.identity == ADMIN_IDENTITY
        assert set(avatar.tags) == {ADMIN_TAG, TENANT_TAG}


class TestTokenConfigured:
    """With a token configured, only that token is the admin identity."""

    @pytest.mark.asyncio
    async def test_the_configured_token_is_admin(self):
        avatar = await _application(API_TOKEN).authenticate_request(_scope(API_TOKEN))
        assert avatar is not None
        assert avatar.identity == ADMIN_IDENTITY

    @pytest.mark.asyncio
    async def test_no_token_is_anonymous(self):
        assert await _application(API_TOKEN).authenticate_request(_scope()) is None
