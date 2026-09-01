# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""Smoke: the harness boots and the app answers on its free port."""

from __future__ import annotations

import httpx
import pytest

from tests import api_routes

pytestmark = pytest.mark.asyncio


async def test_health_answers(api_client):
    response = await api_client.get(api_routes.HEALTH)
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_status_answers_authenticated(api_client):
    response = await api_client.get(api_routes.STATUS)
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["active"] is True


async def test_status_refuses_missing_token(local_server):
    async with httpx.AsyncClient(base_url=local_server.base_url) as bare_client:
        response = await bare_client.get(api_routes.STATUS)
    assert response.status_code == 401
