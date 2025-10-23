#!/bin/bash
# Wrapper to run main.py with test config
export LOG_LEVEL=DEBUG
export ASYNC_MAIL_CONFIG=config_test.ini
exec python3 main.py "$@"
