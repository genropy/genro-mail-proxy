# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Tests for multi-tenant account isolation (Issue #74).

Verifies that accounts with the same id can coexist across different tenants
without conflicts or data loss.
"""

import pytest

from core.mail_proxy.mailproxy_db import MailProxyDb


async def make_db_with_tenants(tmp_path):
    """Create a test database with two tenants."""
    db = MailProxyDb(str(tmp_path / "test.db"))
    await db.init_db()
    await db.table('tenants').add({"id": "tenant_a", "name": "Tenant A"})
    await db.table('tenants').add({"id": "tenant_b", "name": "Tenant B"})
    return db


@pytest.mark.asyncio
async def test_same_account_id_different_tenants(tmp_path):
    """Two tenants can have accounts with the same id."""
    db = await make_db_with_tenants(tmp_path)

    # Add account with id "smtp1" for tenant A
    await db.table('accounts').add({
        "id": "smtp1",
        "tenant_id": "tenant_a",
        "host": "smtp.tenant-a.com",
        "port": 587,
    })

    # Add account with same id "smtp1" for tenant B
    await db.table('accounts').add({
        "id": "smtp1",
        "tenant_id": "tenant_b",
        "host": "smtp.tenant-b.com",
        "port": 587,
    })

    # Both accounts should exist
    accounts_a = await db.table('accounts').list_all("tenant_a")
    accounts_b = await db.table('accounts').list_all("tenant_b")

    assert len(accounts_a) == 1
    assert len(accounts_b) == 1
    assert accounts_a[0]["host"] == "smtp.tenant-a.com"
    assert accounts_b[0]["host"] == "smtp.tenant-b.com"


@pytest.mark.asyncio
async def test_upsert_respects_tenant_isolation(tmp_path):
    """UPSERT updates only the account of the same tenant."""
    db = await make_db_with_tenants(tmp_path)

    # Add account for tenant A
    await db.table('accounts').add({
        "id": "smtp1",
        "tenant_id": "tenant_a",
        "host": "smtp.tenant-a.com",
        "port": 587,
    })

    # Add account for tenant B with same id
    await db.table('accounts').add({
        "id": "smtp1",
        "tenant_id": "tenant_b",
        "host": "smtp.tenant-b.com",
        "port": 587,
    })

    # Update tenant A's account (UPSERT with same id)
    await db.table('accounts').add({
        "id": "smtp1",
        "tenant_id": "tenant_a",
        "host": "smtp.updated-a.com",
        "port": 465,
    })

    # Verify tenant A was updated
    accounts_a = await db.table('accounts').list_all("tenant_a")
    assert accounts_a[0]["host"] == "smtp.updated-a.com"
    assert accounts_a[0]["port"] == 465

    # Verify tenant B was NOT affected
    accounts_b = await db.table('accounts').list_all("tenant_b")
    assert accounts_b[0]["host"] == "smtp.tenant-b.com"
    assert accounts_b[0]["port"] == 587


@pytest.mark.asyncio
async def test_account_requires_tenant_id(tmp_path):
    """Creating an account without tenant_id raises KeyError."""
    db = await make_db_with_tenants(tmp_path)

    with pytest.raises(KeyError):
        await db.table('accounts').add({
            "id": "smtp1",
            # Missing tenant_id
            "host": "smtp.example.com",
            "port": 587,
        })


@pytest.mark.asyncio
async def test_pec_account_requires_tenant_id(tmp_path):
    """Creating a PEC account without tenant_id raises KeyError."""
    db = await make_db_with_tenants(tmp_path)

    with pytest.raises(KeyError):
        await db.table('accounts').add_pec_account({
            "id": "pec1",
            # Missing tenant_id
            "host": "smtp.pec.example.com",
            "port": 587,
            "imap_host": "imap.pec.example.com",
        })


@pytest.mark.asyncio
async def test_pec_accounts_isolated_by_tenant(tmp_path):
    """PEC accounts with same id can coexist across tenants."""
    db = await make_db_with_tenants(tmp_path)

    # Add PEC account for tenant A
    await db.table('accounts').add_pec_account({
        "id": "pec1",
        "tenant_id": "tenant_a",
        "host": "smtp.pec-a.com",
        "port": 587,
        "imap_host": "imap.pec-a.com",
    })

    # Add PEC account with same id for tenant B
    await db.table('accounts').add_pec_account({
        "id": "pec1",
        "tenant_id": "tenant_b",
        "host": "smtp.pec-b.com",
        "port": 587,
        "imap_host": "imap.pec-b.com",
    })

    # Both should exist
    pec_accounts = await db.table('accounts').list_pec_accounts()
    assert len(pec_accounts) == 2

    tenant_a_pec = [a for a in pec_accounts if a["tenant_id"] == "tenant_a"]
    tenant_b_pec = [a for a in pec_accounts if a["tenant_id"] == "tenant_b"]

    assert len(tenant_a_pec) == 1
    assert len(tenant_b_pec) == 1
    assert tenant_a_pec[0]["imap_host"] == "imap.pec-a.com"
    assert tenant_b_pec[0]["imap_host"] == "imap.pec-b.com"


@pytest.mark.asyncio
async def test_get_account_requires_tenant_id(tmp_path):
    """get_account() requires tenant_id and returns the correct account."""
    db = await make_db_with_tenants(tmp_path)

    # Add accounts for both tenants with same id
    await db.table('accounts').add({
        "id": "smtp1",
        "tenant_id": "tenant_a",
        "host": "smtp.tenant-a.com",
        "port": 587,
    })
    await db.table('accounts').add({
        "id": "smtp1",
        "tenant_id": "tenant_b",
        "host": "smtp.tenant-b.com",
        "port": 587,
    })

    # get_account returns the correct account for each tenant
    account_a = await db.table('accounts').get("tenant_a", "smtp1")
    assert account_a["id"] == "smtp1"
    assert account_a["tenant_id"] == "tenant_a"
    assert account_a["host"] == "smtp.tenant-a.com"

    account_b = await db.table('accounts').get("tenant_b", "smtp1")
    assert account_b["id"] == "smtp1"
    assert account_b["tenant_id"] == "tenant_b"
    assert account_b["host"] == "smtp.tenant-b.com"

    # get_account raises ValueError for non-existent tenant/account combo
    with pytest.raises(ValueError):
        await db.table('accounts').get("tenant_a", "nonexistent")
    with pytest.raises(ValueError):
        await db.table('accounts').get("nonexistent", "smtp1")


@pytest.mark.asyncio
async def test_delete_account_only_deletes_for_tenant(tmp_path):
    """delete_account() deletes only the account for the specified tenant."""
    db = await make_db_with_tenants(tmp_path)

    # Add accounts for both tenants with same id
    await db.table('accounts').add({
        "id": "smtp1",
        "tenant_id": "tenant_a",
        "host": "smtp.tenant-a.com",
        "port": 587,
    })
    await db.table('accounts').add({
        "id": "smtp1",
        "tenant_id": "tenant_b",
        "host": "smtp.tenant-b.com",
        "port": 587,
    })

    # Delete only tenant_a's account
    await db.table('accounts').remove("tenant_a", "smtp1")

    # Only tenant_a's account is deleted
    accounts_a = await db.table('accounts').list_all("tenant_a")
    accounts_b = await db.table('accounts').list_all("tenant_b")
    assert len(accounts_a) == 0
    assert len(accounts_b) == 1
    assert accounts_b[0]["host"] == "smtp.tenant-b.com"
