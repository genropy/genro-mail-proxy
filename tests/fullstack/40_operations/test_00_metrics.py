# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""Fullstack tests extracted from test_fullstack_integration.py."""

from __future__ import annotations


import pytest

pytestmark = [pytest.mark.fullstack, pytest.mark.asyncio]


class TestMetrics:
    """Test Prometheus metrics."""

    async def test_metrics_endpoint(self, api_client):
        """Metrics endpoint should return Prometheus format."""
        resp = await api_client.get("/metrics")
        assert resp.status_code == 200

        content = resp.text
        # Should contain Prometheus-style metrics
        assert "mail_proxy" in content or "HELP" in content or "TYPE" in content


# ============================================
# 11. VALIDATION
# ============================================
