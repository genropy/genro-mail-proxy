from prometheus_client import CollectorRegistry

from mail_proxy.prometheus import MailMetrics


def test_mail_metrics_counters_and_gauge():
    """Test basic counter and gauge operations."""
    metrics = MailMetrics()

    metrics.inc_sent("acc1")
    metrics.inc_error(None)
    metrics.inc_deferred("acc2")
    metrics.inc_rate_limited("")
    metrics.set_pending(3)

    output = metrics.generate_latest()
    assert b"gmp_sent_total" in output
    assert b'gmp_pending_messages 3.0' in output


def test_mail_metrics_custom_registry():
    """Test metrics with custom registry."""
    registry = CollectorRegistry()
    metrics = MailMetrics(registry=registry)

    assert metrics.registry is registry
    metrics.inc_sent("test")
    output = metrics.generate_latest()
    assert b"gmp_sent_total" in output


def test_mail_metrics_multiple_increments_same_account():
    """Test multiple increments for the same account."""
    metrics = MailMetrics()

    metrics.inc_sent("acc1")
    metrics.inc_sent("acc1")
    metrics.inc_sent("acc1")

    output = metrics.generate_latest().decode()
    # Counter should show 3.0 for acc1
    assert 'gmp_sent_total{account_id="acc1"} 3.0' in output


def test_mail_metrics_different_accounts():
    """Test metrics track different accounts separately."""
    metrics = MailMetrics()

    metrics.inc_sent("acc1")
    metrics.inc_sent("acc2")
    metrics.inc_error("acc1")
    metrics.inc_error("acc2")
    metrics.inc_error("acc2")

    output = metrics.generate_latest().decode()
    assert 'gmp_sent_total{account_id="acc1"} 1.0' in output
    assert 'gmp_sent_total{account_id="acc2"} 1.0' in output
    assert 'gmp_errors_total{account_id="acc1"} 1.0' in output
    assert 'gmp_errors_total{account_id="acc2"} 2.0' in output


def test_mail_metrics_none_and_empty_fallback_to_default():
    """Test that None and empty string fall back to 'default' account."""
    metrics = MailMetrics()

    metrics.inc_sent(None)
    metrics.inc_sent("")
    metrics.inc_error(None)
    metrics.inc_deferred("")
    metrics.inc_rate_limited(None)

    output = metrics.generate_latest().decode()
    # All should be under "default" account
    assert 'gmp_sent_total{account_id="default"} 2.0' in output
    assert 'gmp_errors_total{account_id="default"} 1.0' in output
    assert 'gmp_deferred_total{account_id="default"} 1.0' in output
    assert 'gmp_rate_limited_total{account_id="default"} 1.0' in output


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
    metrics.inc_sent("test")
    metrics.inc_error("test")
    metrics.inc_deferred("test")
    metrics.inc_rate_limited("test")
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
    metrics.inc_sent("test")

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

    # Initialize account
    metrics.init_account("smtp1")

    # After init_account, all counters should appear with value 0
    output_after = metrics.generate_latest().decode()
    assert 'gmp_sent_total{account_id="smtp1"} 0.0' in output_after
    assert 'gmp_errors_total{account_id="smtp1"} 0.0' in output_after
    assert 'gmp_deferred_total{account_id="smtp1"} 0.0' in output_after
    assert 'gmp_rate_limited_total{account_id="smtp1"} 0.0' in output_after


def test_mail_metrics_init_account_empty_uses_default():
    """Test that init_account with empty string uses 'default' account."""
    metrics = MailMetrics()

    metrics.init_account("")

    output = metrics.generate_latest().decode()
    assert 'gmp_sent_total{account_id="default"} 0.0' in output


def test_mail_metrics_init_multiple_accounts():
    """Test initializing metrics for multiple accounts."""
    metrics = MailMetrics()

    accounts = ["smtp1", "smtp2", "pec-account"]
    for account in accounts:
        metrics.init_account(account)

    output = metrics.generate_latest().decode()

    for account in accounts:
        assert f'gmp_sent_total{{account_id="{account}"}} 0.0' in output
        assert f'gmp_errors_total{{account_id="{account}"}} 0.0' in output
