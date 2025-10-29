from async_mail_service.prometheus import MailMetrics


def test_mail_metrics_counters_and_gauge():
    metrics = MailMetrics()

    metrics.inc_sent("acc1")
    metrics.inc_error(None)
    metrics.inc_deferred("acc2")
    metrics.inc_rate_limited("")
    metrics.set_pending(3)

    output = metrics.generate_latest()
    assert b"gmp_sent_total" in output
    assert b'gmp_pending_messages 3.0' in output
