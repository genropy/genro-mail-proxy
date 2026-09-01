# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""Fullstack tests extracted from test_fullstack_integration.py."""

from __future__ import annotations

import httpx
import pytest

from tests import api_routes
from tests.fullstack.helpers import MAILPROXY_URL

pytestmark = [pytest.mark.fullstack, pytest.mark.asyncio]


class TestMetrics:
    """Test Prometheus metrics."""

    async def test_metrics_endpoint(self, api_client):
        """Metrics endpoint should return Prometheus format."""
        resp = await api_client.get(api_routes.METRICS)
        assert resp.status_code == 200

        content = resp.text
        # Should contain Prometheus-style metrics
        assert "mail_proxy" in content or "HELP" in content or "TYPE" in content

    async def test_metrics_endpoint_no_auth(self):
        """Metrics endpoint must be scrapable without a token.

        Ported from tests/unit/10_api/test_00_api.py: Prometheus scrapes with
        no credentials, so an auth check here would silence the whole metric
        surface.
        """
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{MAILPROXY_URL}{api_routes.METRICS}")
        assert resp.status_code == 200


# ============================================
# 11. VALIDATION
# ============================================
