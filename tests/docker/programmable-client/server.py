# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Programmable fake Genropy client for the fullstack suite.

Two surfaces on one port.

Protocol — what the mail proxy calls on its client:
    POST /proxy_sync              delivery reports; answers what the test programmed
    POST /proxy_get_attachments   attachment bytes, or the error the test programmed

Control — what the tests call here. Test harness only, no contract:
    POST /control/sync_response         queue one answer for /proxy_sync
    POST /control/attachment_response   queue one answer for /proxy_get_attachments
    GET  /control/calls                 the calls received since the last reset
    POST /control/reset                 drop the queues and the call history
    GET  /                              reachability check

Programmed answers are consumed one per call, in order. With the queue empty
/proxy_sync answers DEFAULT_SYNC_RESPONSE — a report cycle nobody programmed
must not block — and /proxy_get_attachments answers 404: an attachment nobody
programmed does not exist.

Every call is recorded before anything else, rejected ones included: what the
proxy sent is what the tests assert on.

Environment variables:
    CLIENT_PORT: port to listen on (default: 8080)
    CLIENT_AUTH_USER: Basic Auth user; auth is enforced only when this is set
    CLIENT_AUTH_PASSWORD: Basic Auth password
"""

from __future__ import annotations

import base64
import json
import logging
import os
import secrets
import time
from typing import Any

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_SYNC_RESPONSE = {"ok": True, "queued": 0}
BASIC_PREFIX = "Basic "


class ProgrammableClient:
    """Fake Genropy endpoint whose answers the tests decide.

    Attributes:
        auth_user: Basic Auth user, or None when auth is not enforced.
        auth_password: Basic Auth password.
        calls: Every call received since the last reset.
        sync_responses: Answers queued for /proxy_sync, consumed in order.
        attachment_responses: Answers queued for /proxy_get_attachments.
        app: The ASGI application serving both surfaces.
    """

    def __init__(self, auth_user: str | None = None, auth_password: str | None = None):
        self.auth_user = auth_user
        self.auth_password = auth_password
        self.calls: list[dict[str, Any]] = []
        self.sync_responses: list[dict[str, Any]] = []
        self.attachment_responses: list[dict[str, Any]] = []
        self.app = FastAPI(title="programmable-client")
        self.app.post("/proxy_sync")(self.receive_sync_report)
        self.app.post("/proxy_get_attachments")(self.receive_attachment_request)
        self.app.post("/control/sync_response")(self.queue_sync_response)
        self.app.post("/control/attachment_response")(self.queue_attachment_response)
        self.app.get("/control/calls")(self.get_call_history)
        self.app.post("/control/reset")(self.reset)
        self.app.get("/")(self.get_liveness)
        logger.info(
            "Programmable client ready (auth %s)",
            "enforced" if self.auth_user else "recorded only",
        )

    def is_authorized(self, request: Request) -> bool:
        """Whether Basic Auth is satisfied — always true when no user is configured."""
        if not self.auth_user:
            return True
        header = request.headers.get("authorization", "")
        if not header.startswith(BASIC_PREFIX):
            return False
        credentials = f"{self.auth_user}:{self.auth_password or ''}".encode()
        expected = base64.b64encode(credentials).decode()
        return secrets.compare_digest(header[len(BASIC_PREFIX):], expected)

    def record_call(self, path: str, body: Any, request: Request) -> None:
        """Append one received call to the history the tests read back."""
        self.calls.append({
            "path": path,
            "body": body,
            "authorization": request.headers.get("authorization"),
            "received_ts": time.time(),
        })

    async def read_body(self, path: str, request: Request) -> Any:
        """Record the call, then hand back its parsed body — None when unparsable.

        The recording comes first so a malformed body still shows up in the
        history: a test asserting "the proxy called us" must read the call and
        judge it, not read an empty history and report the call as absent.
        """
        raw = await request.body()
        try:
            body = json.loads(raw) if raw else None
        except ValueError:
            body = None
        self.record_call(path, body if body is not None else raw.decode(errors="replace"), request)
        return body

    async def receive_sync_report(self, request: Request) -> Response:
        """POST /proxy_sync — record the delivery report, answer what was programmed."""
        body = await self.read_body("/proxy_sync", request)
        if body is None:
            return JSONResponse({"error": "malformed_body"}, status_code=400)
        logger.info("proxy_sync: %d report(s)", len(body.get("delivery_report") or []))
        if not self.is_authorized(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if not self.sync_responses:
            return JSONResponse(DEFAULT_SYNC_RESPONSE)
        programmed = self.sync_responses.pop(0)
        return JSONResponse(
            programmed.get("json", DEFAULT_SYNC_RESPONSE),
            status_code=programmed.get("status", 200),
        )

    async def receive_attachment_request(self, request: Request) -> Response:
        """POST /proxy_get_attachments — record the request, answer bytes or an error."""
        body = await self.read_body("/proxy_get_attachments", request)
        if body is None:
            return JSONResponse({"error": "malformed_body"}, status_code=400)
        logger.info("proxy_get_attachments: %s", body.get("storage_path"))
        if not self.is_authorized(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if not self.attachment_responses:
            return JSONResponse({"error": "not_found"}, status_code=404)
        programmed = self.attachment_responses.pop(0)
        status = programmed.get("status", 200)
        content = programmed.get("content_base64")
        if content is None:
            return JSONResponse({"error": "programmed", "status": status}, status_code=status)
        return Response(
            base64.b64decode(content),
            status_code=status,
            media_type="application/octet-stream",
        )

    async def queue_sync_response(self, request: Request) -> dict[str, Any]:
        """POST /control/sync_response — queue one answer for the next /proxy_sync call."""
        self.sync_responses.append(await request.json())
        return {"ok": True, "queued_answers": len(self.sync_responses)}

    async def queue_attachment_response(self, request: Request) -> dict[str, Any]:
        """POST /control/attachment_response — queue one answer for the next fetch."""
        self.attachment_responses.append(await request.json())
        return {"ok": True, "queued_answers": len(self.attachment_responses)}

    async def get_call_history(self) -> dict[str, Any]:
        """GET /control/calls — every call received since the last reset."""
        return {"calls": self.calls}

    async def get_liveness(self) -> dict[str, Any]:
        """GET / — the suite's reachability check for this service."""
        return {"ok": True, "service": "programmable-client"}

    async def reset(self) -> dict[str, Any]:
        """POST /control/reset — drop the queued answers and the call history."""
        self.calls.clear()
        self.sync_responses.clear()
        self.attachment_responses.clear()
        return {"ok": True}


def main() -> None:
    """Start the programmable client."""
    client = ProgrammableClient(
        auth_user=os.environ.get("CLIENT_AUTH_USER"),
        auth_password=os.environ.get("CLIENT_AUTH_PASSWORD"),
    )
    uvicorn.run(client.app, host="0.0.0.0", port=int(os.environ.get("CLIENT_PORT", "8080")))


if __name__ == "__main__":
    main()
