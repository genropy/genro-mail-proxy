# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Instance configuration entity module.

This module provides the Instance entity for managing service-level
configuration and operations in the mail proxy.

Components:
    InstanceTable: Singleton table for configuration storage.
    InstanceEndpoint: REST API endpoint for service operations.

The instance entity uses a singleton pattern (single row with id=1)
to store instance-wide settings including:
    - Service identity (name, api_token)
    - Edition (ce/ee)
    - Flexible JSON configuration

Example:
    Access via MailProxyBase::

        from core.mail_proxy.proxy_base import MailProxyBase

        proxy = MailProxyBase(db_path=":memory:")
        await proxy.init()

        # Table access for configuration
        instance = proxy.db.table("instance")
        await instance.set_name("production-mailer")
        await instance.set_api_token("secret-token")

        # Endpoint access for service operations
        from core.mail_proxy.entities.instance import InstanceEndpoint
        endpoint = InstanceEndpoint(instance, proxy)
        status = await endpoint.status()

Note:
    Enterprise Edition (EE) extends both classes with bounce detection
    configuration via InstanceTable_EE and InstanceEndpoint_EE mixins.
"""

from .endpoint import InstanceEndpoint
from .table import InstanceTable

__all__ = ["InstanceEndpoint", "InstanceTable"]
