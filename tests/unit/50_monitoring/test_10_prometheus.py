from prometheus_client import CollectorRegistry

from mail_proxy.prometheus import MailMetrics


def test_mail_metrics_counters_and_gauge():
    """Test basic counter and gauge operations."""
    metrics = MailMetrics()

    metrics.inc_sent(account_id="acc1")
    metrics.inc_error()
    metrics.inc_deferred(account_id="acc2")
    metrics.inc_rate_limited(account_id="")
    metrics.set_pending(3)

    output = metrics.generate_latest()
    assert b"gmp_sent_total" in output
    assert b'gmp_pending_messages 3.0' in output


def test_mail_metrics_custom_registry():
    """Test metrics with custom registry."""
    registry = CollectorRegistry()
    metrics = MailMetrics(registry=registry)

    assert metrics.registry is registry
    metrics.inc_sent(account_id="test")
    output = metrics.generate_latest()
    assert b"gmp_sent_total" in output


def test_mail_metrics_multiple_increments_same_account():
    """Test multiple increments for the same account."""
    metrics = MailMetrics()

    metrics.inc_sent(tenant_id="t1", account_id="acc1")
    metrics.inc_sent(tenant_id="t1", account_id="acc1")
    metrics.inc_sent(tenant_id="t1", account_id="acc1")

    output = metrics.generate_latest().decode()
    # Counter should show 3.0 for acc1
    assert 'account_id="acc1"' in output
    assert "3.0" in output


def test_mail_metrics_different_accounts():
    """Test metrics track different accounts separately."""
    metrics = MailMetrics()

    metrics.inc_sent(tenant_id="t1", account_id="acc1")
    metrics.inc_sent(tenant_id="t1", account_id="acc2")
    metrics.inc_error(tenant_id="t1", account_id="acc1")
    metrics.inc_error(tenant_id="t1", account_id="acc2")
    metrics.inc_error(tenant_id="t1", account_id="acc2")

    output = metrics.generate_latest().decode()
    assert 'account_id="acc1"' in output
    assert 'account_id="acc2"' in output


def test_mail_metrics_none_and_empty_fallback_to_default():
    """Test that None and empty string fall back to 'default' account."""
    metrics = MailMetrics()

    metrics.inc_sent()
    metrics.inc_sent(account_id="")
    metrics.inc_error()
    metrics.inc_deferred(account_id="")
    metrics.inc_rate_limited()

    output = metrics.generate_latest().decode()
    # All should be under "default" account
    assert 'account_id="default"' in output
    assert 'tenant_id="default"' in output


def test_mail_metrics_pending_gauge_updates():
    """Test that pending gauge updates correctly (not increments)."""
    metrics = MailMetrics()

    metrics.set_pending(10)
    output1 = metrics.generate_latest().decode()
    assert 'gmp_pending_messages 10.0' in output1

    metrics.set_pending(5)
    output2 = metrics.generate_latest().decode()
    assert 'gmp_pending_messages 5.0' in output2

    metrics.set_pending(0)
    output3 = metrics.generate_latest().decode()
    assert 'gmp_pending_messages 0.0' in output3


def test_mail_metrics_all_counters_present():
    """Test that all expected metrics are present in output."""
    metrics = MailMetrics()

    # Trigger all counters at least once
    metrics.inc_sent(tenant_id="test", account_id="test")
    metrics.inc_error(tenant_id="test", account_id="test")
    metrics.inc_deferred(tenant_id="test", account_id="test")
    metrics.inc_rate_limited(tenant_id="test", account_id="test")
    metrics.set_pending(1)

    output = metrics.generate_latest().decode()

    # All metric names should be present
    assert "gmp_sent_total" in output
    assert "gmp_errors_total" in output
    assert "gmp_deferred_total" in output
    assert "gmp_rate_limited_total" in output
    assert "gmp_pending_messages" in output

    # Help text should be present
    assert "Total sent emails" in output
    assert "Total send errors" in output
    assert "Total deferred emails" in output
    assert "Total rate limited" in output
    assert "Current pending messages" in output


def test_mail_metrics_output_is_bytes():
    """Test that generate_latest returns bytes."""
    metrics = MailMetrics()
    metrics.inc_sent(account_id="test")

    output = metrics.generate_latest()
    assert isinstance(output, bytes)


def test_mail_metrics_init_account():
    """Test that init_account initializes all counters for an account.

    This is critical for Issue #8: Prometheus counters with labels only appear
    in output after being incremented. init_account ensures metrics appear
    even before any email activity.
    """
    metrics = MailMetrics()

    # Before init_account, counters for 'smtp1' should not appear
    output_before = metrics.generate_latest().decode()
    assert 'account_id="smtp1"' not in output_before

    # Initialize account with all labels
    metrics.init_account(
        tenant_id="tenant1",
        tenant_name="Tenant One",
        account_id="smtp1",
        account_name="smtp1",
    )

    # After init_account, all counters should appear with value 0
    output_after = metrics.generate_latest().decode()
    assert 'account_id="smtp1"' in output_after
    assert 'tenant_id="tenant1"' in output_after
    assert 'tenant_name="Tenant One"' in output_after
    assert "0.0" in output_after


def test_mail_metrics_init_account_empty_uses_default():
    """Test that init_account with empty values uses 'default'."""
    metrics = MailMetrics()

    metrics.init_account()  # All defaults

    output = metrics.generate_latest().decode()
    assert 'account_id="default"' in output
    assert 'tenant_id="default"' in output


def test_mail_metrics_init_multiple_accounts():
    """Test initializing metrics for multiple accounts."""
    metrics = MailMetrics()

    accounts = [
        {"tenant_id": "t1", "tenant_name": "Tenant 1", "account_id": "smtp1"},
        {"tenant_id": "t1", "tenant_name": "Tenant 1", "account_id": "smtp2"},
        {"tenant_id": "t2", "tenant_name": "Tenant 2", "account_id": "pec"},
    ]
    for acc in accounts:
        metrics.init_account(**acc)

    output = metrics.generate_latest().decode()

    for acc in accounts:
        assert f'account_id="{acc["account_id"]}"' in output
        assert f'tenant_id="{acc["tenant_id"]}"' in output


def test_mail_metrics_labels_in_output():
    """Test that all four labels appear in metric output."""
    metrics = MailMetrics()

    metrics.inc_sent(
        tenant_id="acme",
        tenant_name="ACME Corp",
        account_id="main-smtp",
        account_name="main-smtp",
    )

    output = metrics.generate_latest().decode()

    # All labels should be present
    assert 'tenant_id="acme"' in output
    assert 'tenant_name="ACME Corp"' in output
    assert 'account_id="main-smtp"' in output
    assert 'account_name="main-smtp"' in output


# -----------------------------------------------------------------------------
# Integration tests with MailProxy
# -----------------------------------------------------------------------------

import pytest
from mail_proxy.core import MailProxy


@pytest.mark.asyncio
async def test_proxy_init_initializes_default_metrics(tmp_path):
    """Test that MailProxy.init() always initializes default account metrics.

    Even when no accounts are configured, /metrics should return counter values
    for the 'default' labels and the pending gauge.
    """
    db_path = tmp_path / "test.db"

    proxy = MailProxy(db_path=str(db_path), test_mode=True)
    await proxy.init()

    try:
        output = proxy.metrics.generate_latest().decode()

        # Default labels should always be initialized
        assert 'account_id="default"' in output
        assert 'tenant_id="default"' in output
        assert "gmp_sent_total" in output
        assert "gmp_errors_total" in output

        # Pending gauge should be present
        assert "gmp_pending_messages 0.0" in output
    finally:
        await proxy.stop()


@pytest.mark.asyncio
async def test_proxy_init_initializes_configured_account_metrics(tmp_path):
    """Test that MailProxy.init() initializes metrics for configured accounts."""
    db_path = tmp_path / "test.db"

    proxy = MailProxy(db_path=str(db_path), test_mode=True)
    await proxy.init()

    try:
        # Add tenant and account
        await proxy.db.add_tenant({"id": "t1", "name": "Test Tenant"})
        await proxy.db.add_account({
            "id": "smtp1",
            "tenant_id": "t1",
            "host": "smtp.example.com",
            "port": 587,
            "user": "user",
            "password": "pass",
        })

        # Re-init metrics
        await proxy._init_account_metrics()

        output = proxy.metrics.generate_latest().decode()

        # Both default and configured account should be present
        assert 'account_id="default"' in output
        assert 'account_id="smtp1"' in output
        assert 'tenant_id="t1"' in output
        assert 'tenant_name="Test Tenant"' in output
    finally:
        await proxy.stop()
