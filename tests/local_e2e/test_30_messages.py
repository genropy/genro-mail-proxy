# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""The four message routes, over HTTP against the in-process app.

Enqueue, the tenant-scoped list, deletion by id, and the retention cleanup.
No delivery is asserted anywhere: this suite has no SMTP server, and what the
0.7.7 transport swap must not change is the HTTP contract — the verb, the
binding, the status code, the shape of the answer. Whether a message ever
leaves is the stress suite's question.
"""

from __future__ import annotations

import uuid

import pytest

from tests import api_routes

pytestmark = pytest.mark.asyncio


def _message(tenant_id: str, account_id: str, **overrides) -> dict:
    """One valid message payload, with a unique id."""
    payload = {
        "id": f"msg-{uuid.uuid4().hex[:12]}",
        "tenant_id": tenant_id,
        "account_id": account_id,
        "from": "sender@local.test",
        "to": ["recipient@local.test"],
        "subject": "Local e2e contract",
        "body": "Body of a message that is never sent.",
    }
    payload.update(overrides)
    return payload


class TestAddMessages:
    """POST /commands/add-messages — the enqueue route."""

    async def test_a_valid_message_is_queued(self, api_client, local_tenants):
        message = _message(local_tenants["tenant1"], local_tenants["account1"])
        resp = await api_client.post(
            api_routes.ADD_MESSAGES, json={"messages": [message]}
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True
        assert body["queued"] == 1

    async def test_several_messages_are_queued_together(self, api_client, local_tenants):
        messages = [
            _message(local_tenants["tenant1"], local_tenants["account1"])
            for _ in range(3)
        ]
        resp = await api_client.post(
            api_routes.ADD_MESSAGES, json={"messages": messages}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["queued"] == 3

    async def test_an_explicit_priority_binds(self, api_client, local_tenants):
        """Both the label and the integer reach the stored row."""
        by_label = _message(
            local_tenants["tenant1"], local_tenants["account1"], priority="high"
        )
        by_number = _message(
            local_tenants["tenant1"], local_tenants["account1"], priority=0
        )
        resp = await api_client.post(
            api_routes.ADD_MESSAGES, json={"messages": [by_label, by_number]}
        )
        assert resp.status_code == 200, resp.text

        resp = await api_client.get(api_routes.messages(local_tenants["tenant1"]))
        stored = {m["id"]: m for m in resp.json()["messages"]}
        assert stored[by_label["id"]]["priority"] == 1
        assert stored[by_number["id"]]["priority"] == 0

    async def test_default_priority_does_not_reach_the_message(
        self, api_client, local_tenants
    ):
        """Characterization: ``default_priority`` is dead on this route.

        ``_validate_enqueue_payload`` runs ``payload.setdefault("priority", 2)``
        before the enqueue loop consults ``default_priority``, so a message
        that names no priority of its own is already carrying 2 by the time
        the default would apply. Asserted as it is, because src/ is untouched
        by this workflow: the 0.7.7 transport swap must not change it either
        way, and the day it is fixed this test fails and says so.
        """
        message = _message(local_tenants["tenant1"], local_tenants["account1"])
        resp = await api_client.post(
            api_routes.ADD_MESSAGES,
            json={"messages": [message], "default_priority": "immediate"},
        )
        assert resp.status_code == 200, resp.text

        resp = await api_client.get(api_routes.messages(local_tenants["tenant1"]))
        stored = {m["id"]: m for m in resp.json()["messages"]}[message["id"]]
        assert stored["priority"] == 2

    async def test_a_message_without_a_subject_is_rejected(
        self, api_client, local_tenants
    ):
        message = _message(
            local_tenants["tenant1"], local_tenants["account1"], subject=""
        )
        resp = await api_client.post(
            api_routes.ADD_MESSAGES, json={"messages": [message]}
        )
        assert resp.status_code == 422

    async def test_a_message_without_a_tenant_id_is_rejected(
        self, api_client, local_tenants
    ):
        message = _message(local_tenants["tenant1"], local_tenants["account1"])
        del message["tenant_id"]
        resp = await api_client.post(
            api_routes.ADD_MESSAGES, json={"messages": [message]}
        )
        assert resp.status_code == 422

    async def test_an_unknown_account_is_refused(self, api_client, local_tenants):
        message = _message(local_tenants["tenant1"], "no-such-account")
        resp = await api_client.post(
            api_routes.ADD_MESSAGES, json={"messages": [message]}
        )
        assert resp.status_code == 400


class TestListMessages:
    """GET /messages — scoped to one tenant, with its two query flags."""

    async def test_the_queued_message_is_listed_with_its_payload(
        self, api_client, local_tenants
    ):
        message = _message(local_tenants["tenant1"], local_tenants["account1"])
        resp = await api_client.post(
            api_routes.ADD_MESSAGES, json={"messages": [message]}
        )
        assert resp.status_code == 200, resp.text

        resp = await api_client.get(api_routes.messages(local_tenants["tenant1"]))
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True

        stored = {m["id"]: m for m in body["messages"]}[message["id"]]
        assert stored["tenant_id"] == local_tenants["tenant1"]
        assert stored["account_id"] == local_tenants["account1"]
        assert stored["message"]["subject"] == "Local e2e contract"
        assert stored["pk"]

    async def test_list_carries_no_other_tenant(self, api_client, local_tenants):
        message = _message(local_tenants["tenant1"], local_tenants["account1"])
        await api_client.post(api_routes.ADD_MESSAGES, json={"messages": [message]})

        resp = await api_client.get(api_routes.messages(local_tenants["tenant1"]))
        messages = resp.json()["messages"]

        assert messages, "the tenant has no message, the assertion proves nothing"
        foreign = [m for m in messages if m["tenant_id"] != local_tenants["tenant1"]]
        assert foreign == [], f"cross-tenant leak in /messages: {foreign}"

    async def test_include_history_adds_the_event_list(self, api_client, local_tenants):
        message = _message(local_tenants["tenant1"], local_tenants["account1"])
        await api_client.post(api_routes.ADD_MESSAGES, json={"messages": [message]})

        resp = await api_client.get(
            api_routes.messages(local_tenants["tenant1"], include_history=True)
        )
        assert resp.status_code == 200
        stored = {m["id"]: m for m in resp.json()["messages"]}[message["id"]]
        assert isinstance(stored["history"], list)

    async def test_active_only_binds_as_a_boolean_query(
        self, api_client, local_tenants
    ):
        """The handler declares the parameter, and declares it a boolean.

        The value it rejects is what makes this discriminating: an undeclared
        query key is simply ignored, so a 0.7.7 transport that dropped
        ``active_only`` would answer 200 here instead of 422. What the filter
        then excludes — messages already sent — cannot be built in this
        harness: test_mode parks the dispatch loop, so no message ever gets an
        smtp_ts. That half belongs to the fullstack suite.
        """
        message = _message(local_tenants["tenant1"], local_tenants["account1"])
        await api_client.post(api_routes.ADD_MESSAGES, json={"messages": [message]})

        resp = await api_client.get(
            api_routes.messages(local_tenants["tenant1"], active_only=True)
        )
        assert resp.status_code == 200
        assert message["id"] in [m["id"] for m in resp.json()["messages"]]

        resp = await api_client.get(
            api_routes.messages(local_tenants["tenant1"], active_only="notabool")
        )
        assert resp.status_code == 422, (
            "active_only accepted a non-boolean: the parameter is no longer "
            "declared, or no longer declared a bool"
        )

    async def test_list_of_an_unknown_tenant_is_empty(self, api_client):
        resp = await api_client.get(api_routes.messages("no-such-tenant"))
        assert resp.status_code == 200
        assert resp.json()["messages"] == []

    async def test_list_requires_tenant_id(self, api_client):
        resp = await api_client.get(api_routes.messages())
        assert resp.status_code == 422


class TestDeleteMessages:
    """POST /commands/delete-messages — by id, within one tenant."""

    async def test_delete_removes_the_named_message(self, api_client, local_tenants):
        message = _message(local_tenants["tenant1"], local_tenants["account1"])
        await api_client.post(api_routes.ADD_MESSAGES, json={"messages": [message]})

        resp = await api_client.post(
            api_routes.delete_messages(local_tenants["tenant1"]),
            json={"ids": [message["id"]]},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True
        assert body["removed"] == 1

        resp = await api_client.get(api_routes.messages(local_tenants["tenant1"]))
        assert message["id"] not in [m["id"] for m in resp.json()["messages"]]

    async def test_an_unknown_id_is_reported_unauthorized(
        self, api_client, local_tenants
    ):
        """Characterization: the scoped delete cannot say "does not exist".

        A row the tenant may not touch and a row nobody has are the same
        answer — ``unauthorized``, with ``not_found`` left empty. It is the
        discreet behaviour (the route never confirms another tenant's message
        exists), and it is asserted as it is so 0.7.7 cannot change it
        unnoticed.
        """
        resp = await api_client.post(
            api_routes.delete_messages(local_tenants["tenant1"]),
            json={"ids": ["no-such-message"]},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["removed"] == 0
        assert body["not_found"] == []
        assert body["unauthorized"] == ["no-such-message"]

    async def test_another_tenant_message_is_reported_unauthorized(
        self, api_client, local_tenants
    ):
        message = _message(local_tenants["tenant1"], local_tenants["account1"])
        await api_client.post(api_routes.ADD_MESSAGES, json={"messages": [message]})

        resp = await api_client.post(
            api_routes.delete_messages(local_tenants["tenant2"]),
            json={"ids": [message["id"]]},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["unauthorized"] == [message["id"]]

        # The message survived the attempt.
        resp = await api_client.get(api_routes.messages(local_tenants["tenant1"]))
        assert message["id"] in [m["id"] for m in resp.json()["messages"]]

    async def test_delete_requires_tenant_id(self, api_client):
        resp = await api_client.post(
            api_routes.delete_messages(), json={"ids": ["msg-1"]}
        )
        assert resp.status_code == 422


class TestCleanupMessages:
    """POST /commands/cleanup-messages — the retention sweep."""

    async def test_cleanup_answers_a_removed_count(self, api_client, local_tenants):
        resp = await api_client.post(
            api_routes.cleanup_messages(local_tenants["tenant1"]),
            json={"older_than_seconds": 3600},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True
        assert isinstance(body["removed"], int)

    async def test_cleanup_spares_a_message_still_pending(
        self, api_client, local_tenants
    ):
        """Only reported messages are swept: a fresh one is untouched."""
        message = _message(local_tenants["tenant1"], local_tenants["account1"])
        await api_client.post(api_routes.ADD_MESSAGES, json={"messages": [message]})

        resp = await api_client.post(
            api_routes.cleanup_messages(local_tenants["tenant1"]),
            json={"older_than_seconds": 0},
        )
        assert resp.status_code == 200, resp.text

        resp = await api_client.get(api_routes.messages(local_tenants["tenant1"]))
        assert message["id"] in [m["id"] for m in resp.json()["messages"]]

    async def test_cleanup_requires_tenant_id(self, api_client):
        resp = await api_client.post(
            api_routes.cleanup_messages(), json={"older_than_seconds": 3600}
        )
        assert resp.status_code == 422
