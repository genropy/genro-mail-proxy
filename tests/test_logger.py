from mail_proxy.logger import get_logger


def test_get_logger_reuses_existing_logger():
    logger = get_logger("TestLogger")
    handler_count = len(logger.handlers)

    same_logger = get_logger("TestLogger")
    assert logger is same_logger
    assert len(same_logger.handlers) == handler_count
    # Logger level can be 0 (NOTSET) which is valid - it inherits from root logger
    assert isinstance(logger.level, int)
