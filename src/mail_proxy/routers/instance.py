# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""InstanceRoutes: the singleton configuration row and the bounce reload.

``/instance`` is read and written at the same address, so one handler branches
on ``request.method`` — the shared-path shim, confined to preserving the v1 URL.
``/instance/reload-bounce`` has a segment of its own and is a plain entry.

The whole group is admin-only, and the read never returns
``bounce_imap_password``: the column exists, the answer drops it.
"""

from __future__ import annotations

from typing import Any

from genro_asgi.exceptions import HTTPException
from genro_routes import route

from ..auth_tags import ADMIN_TAG
from ..bounce import BounceConfig
from ..http_schema import BasicOkResponse, InstanceInfo, InstanceUpdatePayload
from .base import ProxyRoutes

__all__ = ["InstanceRoutes"]


class InstanceRoutes(ProxyRoutes):
    """``/instance`` — the singleton row, read and written, plus the reload."""

    @route(auth_rule=ADMIN_TAG)
    async def index(self, *, request: Any, body_data: Any = None, **kwargs: Any) -> dict[str, Any]:
        """Dispatch the two ``/instance`` routes on the verb."""
        if request.method == "GET":
            return await self.get_instance()
        if request.method == "PUT":
            return await self.update_instance(body_data)
        raise HTTPException(405, f"{request.method} not allowed on /instance")

    async def get_instance(self) -> dict[str, Any]:
        """Return the instance configuration, or its defaults when unwritten.

        The two flags are stored as integers and answered as booleans, and the
        IMAP password is removed before the row leaves the process.
        """
        result = await self.core.handle_command("getInstance", {})
        if not result.get("ok"):
            return InstanceInfo().model_dump(mode="json", exclude_none=True)
        result.pop("ok", None)
        if "bounce_enabled" in result:
            result["bounce_enabled"] = bool(result["bounce_enabled"])
        if "bounce_imap_ssl" in result:
            result["bounce_imap_ssl"] = bool(result["bounce_imap_ssl"])
        result.pop("bounce_imap_password", None)
        return InstanceInfo(**result).model_dump(mode="json", exclude_none=True)

    async def update_instance(self, body_data: Any) -> dict[str, Any]:
        """Apply the fields the body names, leaving every other one alone."""
        payload = self.validated_payload(InstanceUpdatePayload, body_data)
        update_data = payload.model_dump(exclude_none=True)
        if "bounce_enabled" in update_data:
            update_data["bounce_enabled"] = 1 if update_data["bounce_enabled"] else 0
        if "bounce_imap_ssl" in update_data:
            update_data["bounce_imap_ssl"] = 1 if update_data["bounce_imap_ssl"] else 0
        result = await self.core.handle_command("updateInstance", update_data)
        return BasicOkResponse.model_validate(result).model_dump(exclude_none=True)

    @route(name="reload-bounce", auth_rule=ADMIN_TAG)
    async def reload_bounce(self, **kwargs: Any) -> dict[str, Any]:
        """Restart the bounce receiver on what ``PUT /instance`` stored.

        Bounce disabled tears down the running receiver and answers ok; bounce
        enabled without an IMAP host is a 400, since there is nothing to poll.
        """
        bounce_config = await self.core.db.instance.get_bounce_config()
        if not bounce_config.get("enabled"):
            await self.core._stop_bounce_receiver()
            return BasicOkResponse(ok=True).model_dump(exclude_none=True)

        host = bounce_config.get("imap_host")
        if not host:
            raise HTTPException(400, "Bounce enabled but imap_host not configured")

        await self.core._stop_bounce_receiver()
        config = BounceConfig(
            host=host,
            port=bounce_config.get("imap_port") or 993,
            user=bounce_config.get("imap_user") or "",
            password=bounce_config.get("imap_password") or "",
            use_ssl=bounce_config.get("imap_ssl", True),
            poll_interval=bounce_config.get("poll_interval") or 60,
        )
        self.core.configure_bounce_receiver(config)
        await self.core._start_bounce_receiver()
        return BasicOkResponse(ok=True).model_dump(exclude_none=True)
