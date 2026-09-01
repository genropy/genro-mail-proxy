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

The coverage check reads the ROUTER, not a source file. Two halves:

* every URL the tables name resolves to a live route — no table entry points
  at an address that does not exist;
* the set of route entries the router publishes is the one written in
  ``V1_ENTRIES`` — adding a route breaks this and forces a table entry.

What this can no longer see, and the FastAPI decorators could: the verb.
genro-asgi resolves on the path alone, so the six routes sharing ``/tenant``
are one router entry, and a seventh verb added inside that handler would not
show up here. The verb dimension of the contract is asserted by the suites
that call the routes (test_10 through test_50), not by this count.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import pytest

from mail_proxy.core import MailProxy
from mail_proxy.mail_proxy_application import MailProxyApplication
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


# Every route entry the v1 branch publishes, as router paths. The three shims
# appear once each: /tenant serves six routes, /account and /instance two each.
V1_ENTRIES = {
    "account/index",
    "accounts/index",
    "command-log/export",
    "command-log/index",
    "commands/activate",
    "commands/add-messages",
    "commands/cleanup-messages",
    "commands/delete-messages",
    "commands/run-now",
    "commands/suspend",
    "health",
    "instance/index",
    "instance/reload-bounce",
    "messages/index",
    "metrics",
    "status",
    "tenant/index",
    "tenants/index",
    "tenants/sync-status",
}

# Both tags, so the walk sees the ruled entries too: an unfiltered walk shows
# only what carries no auth_rule.
ALL_TAGS = "ADMIN,TENANT"


@pytest.fixture(scope="module")
def v1_router(tmp_path_factory):
    """The router of a throwaway application, for introspection only.

    Never served and never started: the tree is built at construction, which
    is all the coverage check reads.
    """
    db_path = tmp_path_factory.mktemp("auth_coverage") / "mail_proxy.db"
    core = MailProxy(db_path=str(db_path), test_mode=True)
    return MailProxyApplication(core=core, api_token="unused").route


def _entry_paths(node, prefix=""):
    """Every callable entry under a ``nodes()`` subtree, as router paths."""
    for name in node.get("entries") or {}:
        yield f"{prefix}{name}"
    for child, subtree in (node.get("routers") or {}).items():
        yield from _entry_paths(subtree, f"{prefix}{child}/")


def _published_entries(router):
    """The route entries of the v1 branch, derived from the router."""
    tree = router.nodes(_eager=True, auth_tags=ALL_TAGS)
    v1 = tree["routers"]["v1"]
    return set(_entry_paths(v1))


def _router_path(url):
    """The router path a table URL resolves against: no prefix, no query."""
    return f"v1{urlsplit(url).path}"


async def test_the_tables_cover_every_route_once(v1_router):
    """The tables name live routes, and no route escapes them."""
    labels = _ids(OPEN_ROUTES) + _ids(TOKEN_PROTECTED_ROUTES)
    assert len(labels) == len(set(labels)), "a route appears in two tables"
    assert len(labels) == 26

    published = _published_entries(v1_router)
    assert published == V1_ENTRIES, (
        f"the router and V1_ENTRIES disagree: "
        f"published but not listed {sorted(published - V1_ENTRIES)}, "
        f"listed but not published {sorted(V1_ENTRIES - published)}"
    )

    for label, _method, url, _body in OPEN_ROUTES + TOKEN_PROTECTED_ROUTES:
        node = v1_router.node(_router_path(url), auth_tags=ALL_TAGS)
        assert node.error is None, f"{label} names no live route: {node.error}"


async def test_every_route_is_reached_by_a_table(v1_router):
    """No route entry is left without a table entry exercising its auth."""
    reached = {
        _router_path(url).removeprefix("v1/")
        for _label, _method, url, _body in OPEN_ROUTES + TOKEN_PROTECTED_ROUTES
    }
    resolved = set()
    for entry in _published_entries(v1_router):
        segment = entry.removesuffix("/index")
        assert any(
            path == segment or path.startswith(f"{segment}/") for path in reached
        ), f"no table entry reaches the route {entry}"
        resolved.add(entry)
    assert resolved == V1_ENTRIES


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
