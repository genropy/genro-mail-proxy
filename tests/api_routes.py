# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""Every URL the fullstack suite calls, in one place.

The suite talks to the mail proxy from the outside, so a route path written
by hand in a test is a duplicate of the API's own contract. This module holds
them all: the transport swap of 0.7.7 edits this file and nothing else, and
whatever still fails afterwards is a real regression.

Two blocks, deliberately separate:

* **API routes** — the 26 routes ``src/mail_proxy/api.py`` publishes. A route
  whose handler takes no query parameter is a constant; every other route is
  a function that returns the full relative URL, query string included.
* **Client-protocol paths** — the paths the proxy calls *on the client*, not
  routes of the proxy. They belong to the fake client, not to the API.

Two dead-route literals are deliberately NOT here: ``/messages/{id}/events``
(no such route; ``helpers.wait_for_pec_event`` is rewritten in Phase 3) and
``GET /account?id=`` in ``40_operations/test_20_rate_limiting.py``. Giving
them an entry would preserve paths that have to disappear.
"""

from __future__ import annotations

from urllib.parse import urlencode


def _with_query(path: str, **params: object) -> str:
    """Append the parameters that were given a value; drop the ones left None."""
    given = {k: v for k, v in params.items() if v is not None}
    if not given:
        return path
    return f"{path}?{urlencode(given)}"


# ============================================
# API ROUTES — fixed paths (handler takes no query parameter)
# ============================================

HEALTH = "/health"
STATUS = "/status"
METRICS = "/metrics"

TENANT = "/tenant"                                  # POST — create a tenant
ACCOUNT = "/account"                                # POST — create an account

TENANTS_SYNC_STATUS = "/tenants/sync-status"        # GET

INSTANCE = "/instance"                              # GET, PUT
INSTANCE_RELOAD_BOUNCE = "/instance/reload-bounce"  # POST

ADD_MESSAGES = "/commands/add-messages"             # POST — payload only


# ============================================
# API ROUTES — parameterised paths
# ============================================

def tenants(active_only: bool | None = None) -> str:
    """GET /tenants — every tenant, optionally the active ones only."""
    return _with_query("/tenants", active_only=active_only)


def tenant(tenant_id: str) -> str:
    """GET, PUT, DELETE /tenant/{tenant_id}."""
    return f"/tenant/{tenant_id}"


def tenant_api_key(tenant_id: str) -> str:
    """POST, DELETE /tenant/{tenant_id}/api-key."""
    return f"/tenant/{tenant_id}/api-key"


def accounts(tenant_id: str | None = None) -> str:
    """GET /accounts — the SMTP accounts of one tenant.

    ``tenant_id=None`` yields the bare route, which the API answers 422: the
    parameter is required, and a test asserting that needs the URL without it.
    """
    return _with_query("/accounts", tenant_id=tenant_id)


def account(account_id: str, tenant_id: str | None = None) -> str:
    """DELETE /account/{account_id} — the tenant scopes the deletion."""
    return _with_query(f"/account/{account_id}", tenant_id=tenant_id)


def messages(
    tenant_id: str | None = None,
    active_only: bool | str | None = None,
    include_history: bool | None = None,
) -> str:
    """GET /messages — the message queue of one tenant.

    ``tenant_id=None`` yields the bare route, which the API answers 422.
    ``active_only`` also takes a string, so a test can send a value the
    handler must refuse and prove the parameter is still declared a boolean.
    """
    return _with_query(
        "/messages",
        tenant_id=tenant_id,
        active_only=active_only,
        include_history=include_history,
    )


def command_log(
    tenant_id: str | None = None,
    since_ts: int | None = None,
    until_ts: int | None = None,
    endpoint_filter: str | None = None,
    limit: int | None = None,
) -> str:
    """GET /command-log — the audit trail of the command endpoints."""
    return _with_query(
        "/command-log",
        tenant_id=tenant_id,
        since_ts=since_ts,
        until_ts=until_ts,
        endpoint_filter=endpoint_filter,
        limit=limit,
    )


def command_log_export(
    tenant_id: str | None = None,
    since_ts: int | None = None,
    until_ts: int | None = None,
) -> str:
    """GET /command-log/export — the same trail as a downloadable file."""
    return _with_query(
        "/command-log/export",
        tenant_id=tenant_id,
        since_ts=since_ts,
        until_ts=until_ts,
    )


def run_now(tenant_id: str | None = None) -> str:
    """POST /commands/run-now — dispatch this tenant's queue immediately."""
    return _with_query("/commands/run-now", tenant_id=tenant_id)


def suspend(tenant_id: str | None = None, batch_code: str | None = None) -> str:
    """POST /commands/suspend — hold a tenant, or one batch of it."""
    return _with_query("/commands/suspend", tenant_id=tenant_id, batch_code=batch_code)


def activate(tenant_id: str | None = None, batch_code: str | None = None) -> str:
    """POST /commands/activate — release what suspend held."""
    return _with_query("/commands/activate", tenant_id=tenant_id, batch_code=batch_code)


def delete_messages(tenant_id: str | None = None) -> str:
    """POST /commands/delete-messages — drop the messages the payload names."""
    return _with_query("/commands/delete-messages", tenant_id=tenant_id)


def cleanup_messages(tenant_id: str | None = None) -> str:
    """POST /commands/cleanup-messages — apply the retention policy."""
    return _with_query("/commands/cleanup-messages", tenant_id=tenant_id)


# ============================================
# CLIENT-PROTOCOL PATHS
# ============================================
# What the proxy calls on the Genropy client, not routes of the proxy.

CLIENT_SYNC_PATH = "/proxy_sync"
CLIENT_ATTACHMENT_PATH = "/proxy_get_attachments"


# ============================================
# CONTROL SURFACE OF THE FAKE CLIENT
# ============================================
# Test harness only — no contract of any kind. The programmable client of
# tests/docker/programmable-client serves these so a test can decide its
# answers and read back what the proxy sent it.

CONTROL_SYNC_RESPONSE = "/control/sync_response"
CONTROL_ATTACHMENT_RESPONSE = "/control/attachment_response"
CONTROL_CALLS = "/control/calls"
CONTROL_RESET = "/control/reset"
