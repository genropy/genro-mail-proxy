from async_mail_service.logger import get_logger


def test_get_logger_reuses_existing_logger():
    logger = get_logger("TestLogger")
    handler_count = len(logger.handlers)

    same_logger = get_logger("TestLogger")
    assert logger is same_logger
    assert len(same_logger.handlers) == handler_count
    assert logger.level != 0
