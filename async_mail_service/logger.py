"""Logging helpers for the async mail service."""

import logging

def get_logger(name: str = "AsyncMailService") -> logging.Logger:
    """Return a configured :class:`logging.Logger` instance.

    Note: Logging configuration should be done via logging.basicConfig()
    in the main entry point (main.py) to avoid duplicate handlers.
    """
    return logging.getLogger(name)
