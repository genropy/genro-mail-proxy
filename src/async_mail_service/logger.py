"""Logging utilities for the async mail service.

This module provides a centralized logging configuration helper. The actual
logging setup (level, handlers, format) should be configured via
``logging.basicConfig()`` in the main entry point to avoid duplicate handlers.

Example:
    Typical usage in a module::

        from async_mail_service.logger import get_logger

        logger = get_logger("MyModule")
        logger.info("Operation completed")
"""

import logging


def get_logger(name: str = "AsyncMailService") -> logging.Logger:
    """Retrieve a configured logger instance.

    This function returns a standard library logger with the specified name.
    It does not configure handlers or formatters; that responsibility lies
    with the application entry point.

    Args:
        name: The logger name. Defaults to "AsyncMailService".

    Returns:
        A ``logging.Logger`` instance bound to the given name.

    Example:
        >>> logger = get_logger("smtp")
        >>> logger.info("Connection established")
    """
    return logging.getLogger(name)
