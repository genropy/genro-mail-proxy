# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Tests for tools.prometheus.metrics module."""

import pytest
from prometheus_client import CollectorRegistry

from tools.prometheus.metrics import MailMetrics


@pytest.fixture
def metrics():
    """Create a MailMetrics instance with isolated registry."""
    registry = CollectorRegistry()
    return MailMetrics(registry=registry)


class TestMailMetrics:
    """Tests for MailMetrics class."""

    def test_init_creates_metrics(self, metrics):
        """Should create all expected metrics."""
        assert metrics.sent is not None
        assert metrics.errors is not None
        assert metrics.deferred is not None
        assert metrics.rate_limited is not None
        assert metrics.pending is not None

    def test_inc_sent_increments_counter(self, metrics):
        """inc_sent should increment the sent counter."""
        metrics.inc_sent(tenant_id="t1", account_id="a1")
        metrics.inc_sent(tenant_id="t1", account_id="a1")

        output = metrics.generate_latest().decode()
        assert 'gmp_sent_total{account_id="a1"' in output
        assert "2.0" in output

    def test_inc_error_increments_counter(self, metrics):
        """inc_error should increment the errors counter."""
        metrics.inc_error(tenant_id="t1", account_id="a1")

        output = metrics.generate_latest().decode()
        assert "gmp_errors_total" in output

    def test_inc_deferred_increments_counter(self, metrics):
        """inc_deferred should increment the deferred counter."""
        metrics.inc_deferred(tenant_id="t1", account_id="a1")

        output = metrics.generate_latest().decode()
        assert "gmp_deferred_total" in output

    def test_inc_rate_limited_increments_counter(self, metrics):
        """inc_rate_limited should increment the rate_limited counter."""
        metrics.inc_rate_limited(tenant_id="t1", account_id="a1")

        output = metrics.generate_latest().decode()
        assert "gmp_rate_limited_total" in output

    def test_set_pending_sets_gauge(self, metrics):
        """set_pending should set the pending gauge value."""
        metrics.set_pending(42)

        output = metrics.generate_latest().decode()
        assert "gmp_pending_messages 42.0" in output

    def test_init_account_initializes_labels(self, metrics):
        """init_account should create label combinations with zero values."""
        metrics.init_account(
            tenant_id="tenant1",
            tenant_name="Tenant One",
            account_id="account1",
            account_name="Account One",
        )

        output = metrics.generate_latest().decode()
        # Labels should exist in output
        assert "tenant1" in output
        assert "account1" in output

    def test_default_labels_when_none_provided(self, metrics):
        """Should use 'default' when tenant_id or account_id not provided."""
        metrics.inc_sent()  # No arguments

        output = metrics.generate_latest().decode()
        assert 'tenant_id="default"' in output
        assert 'account_id="default"' in output

    def test_tenant_name_defaults_to_tenant_id(self, metrics):
        """tenant_name should default to tenant_id if not provided."""
        metrics.inc_sent(tenant_id="my-tenant", account_id="acc1")

        output = metrics.generate_latest().decode()
        assert 'tenant_name="my-tenant"' in output

    def test_account_name_defaults_to_account_id(self, metrics):
        """account_name should default to account_id if not provided."""
        metrics.inc_sent(tenant_id="t1", account_id="my-account")

        output = metrics.generate_latest().decode()
        assert 'account_name="my-account"' in output

    def test_custom_names_are_used(self, metrics):
        """Custom tenant_name and account_name should be used."""
        metrics.inc_sent(
            tenant_id="t1",
            tenant_name="My Tenant",
            account_id="a1",
            account_name="My Account",
        )

        output = metrics.generate_latest().decode()
        assert 'tenant_name="My Tenant"' in output
        assert 'account_name="My Account"' in output

    def test_generate_latest_returns_bytes(self, metrics):
        """generate_latest should return bytes."""
        metrics.inc_sent(tenant_id="t1", account_id="a1")

        result = metrics.generate_latest()
        assert isinstance(result, bytes)

    def test_multiple_accounts_tracked_separately(self, metrics):
        """Different accounts should have separate counters."""
        metrics.inc_sent(tenant_id="t1", account_id="a1")
        metrics.inc_sent(tenant_id="t1", account_id="a1")
        metrics.inc_sent(tenant_id="t1", account_id="a2")

        output = metrics.generate_latest().decode()
        # Both accounts should appear
        assert 'account_id="a1"' in output
        assert 'account_id="a2"' in output

    def test_multiple_tenants_tracked_separately(self, metrics):
        """Different tenants should have separate counters."""
        metrics.inc_sent(tenant_id="tenant1", account_id="a1")
        metrics.inc_error(tenant_id="tenant2", account_id="a1")

        output = metrics.generate_latest().decode()
        assert 'tenant_id="tenant1"' in output
        assert 'tenant_id="tenant2"' in output

    def test_default_registry_if_none_provided(self):
        """Should create own registry if none provided."""
        metrics = MailMetrics()
        assert metrics.registry is not None
        # Should be able to generate output
        output = metrics.generate_latest()
        assert isinstance(output, bytes)
