# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: BSL-1.1
"""Pytest fixtures for Enterprise Edition tests.

This module imports fixtures from fullstack tests for integration tests
and provides EE-specific fixtures.
"""

# Import fixtures from fullstack for integration tests (60_imap, 70_pec)
# pylint: disable=unused-import
from tests.fullstack.conftest import (  # noqa: F401
    api_headers,
    api_client,
    setup_test_tenants,
    setup_bounce_tenant,
    setup_pec_tenant,
)
