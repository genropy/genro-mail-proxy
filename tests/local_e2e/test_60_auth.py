# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""The auth contract of all 26 routes, over HTTP against the in-process app.

Three tables, one per class the app draws:

* **open** — ``GET /health`` and ``GET /metrics`` carry no dependency and
  answer without a token.
* **authenticated** — the global token or a tenant key opens them.
* **admin** — the global token only; a valid tenant key is refused 403.

Every route of the API appears in exactly one table, so the count is checked
here too: the tables must add up to 26. Each entry carries a request that
would succeed with the right token, so the only failing dimension is auth.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import mail_proxy.api
from tests import api_routes

pytestmark = pytest.mark.asyncio

_MESSAGE = {
    "id": "auth-probe",
    "tenant_id": "auth-probe-tenant",
    "account_id": "auth-probe-account",
    "from": "sender@local.test",
    "to": ["recipient@local.test"],
    "subject": "Auth probe",
}

# (label, method, url, json body or None)
OPEN_ROUTES = [
    ("GET /health", "GET", api_routes.HEALTH, None),
    ("GET /metrics", "GET", api_routes.METRICS, None),
]

AUTHENTICATED_ROUTES = [
    ("GET /status", "GET", api_routes.STATUS, None),
    ("POST /commands/run-now", "POST", api_routes.run_now(), None),
    ("POST /commands/suspend", "POST", api_routes.suspend("auth-probe-tenant"), None),
    ("POST /commands/activate", "POST", api_routes.activate("auth-probe-tenant"), None),
    ("POST /commands/add-messages", "POST", api_routes.ADD_MESSAGES, {"messages": [_MESSAGE]}),
    ("POST /commands/delete-messages", "POST", api_routes.delete_messages("auth-probe-tenant"), {"ids": ["auth-probe"]}),
    ("POST /commands/cleanup-messages", "POST", api_routes.cleanup_messages("auth-probe-tenant"), {"older_than_seconds": 3600}),
    ("POST /account", "POST", api_routes.ACCOUNT, {"id": "auth-probe-account", "tenant_id": "auth-probe-tenant", "host": "127.0.0.1", "port": 9}),
    ("GET /accounts", "GET", api_routes.accounts("auth-probe-tenant"), None),
    ("DELETE /account/{account_id}", "DELETE", api_routes.account("auth-probe-account", tenant_id="auth-probe-tenant"), None),
    ("GET /messages", "GET", api_routes.messages("auth-probe-tenant"), None),
    ("GET /tenant/{tenant_id}", "GET", api_routes.tenant("auth-probe-tenant"), None),
    ("PUT /tenant/{tenant_id}", "PUT", api_routes.tenant("auth-probe-tenant"), {"name": "Auth Probe"}),
]

ADMIN_ROUTES = [
    ("POST /tenant", "POST", api_routes.TENANT, {"id": "auth-probe-tenant", "name": "Auth Probe"}),
    ("GET /tenants", "GET", api_routes.tenants(), None),
    ("GET /tenants/sync-status", "GET", api_routes.TENANTS_SYNC_STATUS, None),
    ("DELETE /tenant/{tenant_id}", "DELETE", api_routes.tenant("auth-probe-tenant"), None),
    ("POST /tenant/{tenant_id}/api-key", "POST", api_routes.tenant_api_key("auth-probe-tenant"), None),
    ("DELETE /tenant/{tenant_id}/api-key", "DELETE", api_routes.tenant_api_key("auth-probe-tenant"), None),
    ("GET /instance", "GET", api_routes.INSTANCE, None),
    ("PUT /instance", "PUT", api_routes.INSTANCE, {"name": "Auth Probe"}),
    ("POST /instance/reload-bounce", "POST", api_routes.INSTANCE_RELOAD_BOUNCE, None),
    ("GET /command-log", "GET", api_routes.command_log(), None),
    ("GET /command-log/export", "GET", api_routes.command_log_export(), None),
]

TOKEN_PROTECTED_ROUTES = AUTHENTICATED_ROUTES + ADMIN_ROUTES


def _ids(table):
    return [entry[0] for entry in table]


async def _call(client, method, url, body, headers=None):
    return await client.request(method, url, json=body, headers=headers)


ROUTE_DECORATOR = re.compile(
    r"^\s*@(api|router)\.(get|post|put|delete|patch)\(\s*[\"\'](?P<path>[^\"\']+)[\"\']",
    re.MULTILINE,
)

# The commands router is mounted under this prefix (src/mail_proxy/api.py).
ROUTER_PREFIX = "/commands"


def _published_routes():
    """Every route api.py declares, as the labels the tables use.

    Read from the decorators of the source, not from the tables: a 27th route
    added without a table entry must fail here, or its auth contract goes
    unexercised while the suite stays green. The paths are not written down
    again — they are derived — so they still live only in tests/api_routes.py
    as far as the requests are concerned.
    """
    source = Path(mail_proxy.api.__file__).read_text(encoding="utf-8")
    labels = set()
    for match in ROUTE_DECORATOR.finditer(source):
        prefix = ROUTER_PREFIX if match.group(1) == "router" else ""
        labels.add(f"{match.group(2).upper()} {prefix}{match.group('path')}")
    return labels


async def test_the_tables_cover_every_route_once():
    """The three tables cover exactly the routes api.py publishes, once each."""
    labels = _ids(OPEN_ROUTES) + _ids(TOKEN_PROTECTED_ROUTES)
    assert len(labels) == len(set(labels)), "a route appears in two tables"

    published = _published_routes()
    assert published, "no route decorator found in api.py — the regex is stale"
    assert set(labels) == published, (
        f"tables and api.py disagree: "
        f"missing from the tables {sorted(published - set(labels))}, "
        f"not published {sorted(set(labels) - published)}"
    )
    assert len(labels) == 26


class TestOpenRoutes:
    """The two routes that carry no auth dependency."""

    @pytest.mark.parametrize(
        "method,url,body", [e[1:] for e in OPEN_ROUTES], ids=_ids(OPEN_ROUTES)
    )
    async def test_answers_without_a_token(self, bare_client, method, url, body):
        resp = await _call(bare_client, method, url, body)
        assert resp.status_code == 200


class TestMissingToken:
    """Every protected route refuses the request that carries no token."""

    @pytest.mark.parametrize(
        "method,url,body",
        [e[1:] for e in TOKEN_PROTECTED_ROUTES],
        ids=_ids(TOKEN_PROTECTED_ROUTES),
    )
    async def test_answers_401(self, bare_client, method, url, body):
        resp = await _call(bare_client, method, url, body)
        assert resp.status_code == 401


class TestWrongToken:
    """A token that matches nothing is refused exactly like none at all."""

    @pytest.mark.parametrize(
        "method,url,body",
        [e[1:] for e in TOKEN_PROTECTED_ROUTES],
        ids=_ids(TOKEN_PROTECTED_ROUTES),
    )
    async def test_answers_401(self, bare_client, method, url, body):
        resp = await _call(
            bare_client, method, url, body, headers={"X-API-Token": "not-a-token"}
        )
        assert resp.status_code == 401


class TestTenantTokenOnAdminRoutes:
    """A valid tenant key is a real token on a scope it does not have."""

    @pytest.mark.parametrize(
        "method,url,body", [e[1:] for e in ADMIN_ROUTES], ids=_ids(ADMIN_ROUTES)
    )
    async def test_answers_403(
        self, api_client, bare_client, throwaway_tenant, method, url, body
    ):
        resp = await api_client.post(api_routes.tenant_api_key(throwaway_tenant))
        assert resp.status_code == 200, resp.text
        tenant_token = resp.json()["api_key"]

        resp = await _call(
            bare_client, method, url, body, headers={"X-API-Token": tenant_token}
        )
        assert resp.status_code == 403


class TestTenantTokenScope:
    """A tenant key opens its own tenant's routes and nobody else's."""

    async def test_its_own_tenant_is_readable(
        self, api_client, bare_client, throwaway_tenant
    ):
        resp = await api_client.post(api_routes.tenant_api_key(throwaway_tenant))
        tenant_token = resp.json()["api_key"]

        resp = await bare_client.get(
            api_routes.tenant(throwaway_tenant),
            headers={"X-API-Token": tenant_token},
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == throwaway_tenant

    async def test_another_tenant_is_refused(
        self, api_client, bare_client, throwaway_tenant, local_tenants
    ):
        resp = await api_client.post(api_routes.tenant_api_key(throwaway_tenant))
        tenant_token = resp.json()["api_key"]

        resp = await bare_client.get(
            api_routes.tenant(local_tenants["tenant1"]),
            headers={"X-API-Token": tenant_token},
        )
        assert resp.status_code == 401

    async def test_another_tenant_messages_are_refused(
        self, api_client, bare_client, throwaway_tenant, local_tenants
    ):
        resp = await api_client.post(api_routes.tenant_api_key(throwaway_tenant))
        tenant_token = resp.json()["api_key"]

        resp = await bare_client.get(
            api_routes.messages(local_tenants["tenant1"]),
            headers={"X-API-Token": tenant_token},
        )
        assert resp.status_code == 401

    async def test_another_tenant_accounts_are_refused(
        self, api_client, bare_client, throwaway_tenant, local_tenants
    ):
        resp = await api_client.post(api_routes.tenant_api_key(throwaway_tenant))
        tenant_token = resp.json()["api_key"]

        resp = await bare_client.get(
            api_routes.accounts(local_tenants["tenant1"]),
            headers={"X-API-Token": tenant_token},
        )
        assert resp.status_code == 401

    async def test_another_tenant_messages_cannot_be_deleted(
        self, api_client, bare_client, throwaway_tenant, local_tenants
    ):
        resp = await api_client.post(api_routes.tenant_api_key(throwaway_tenant))
        tenant_token = resp.json()["api_key"]

        resp = await bare_client.post(
            api_routes.delete_messages(local_tenants["tenant1"]),
            json={"ids": ["whatever"]},
            headers={"X-API-Token": tenant_token},
        )
        assert resp.status_code == 401
