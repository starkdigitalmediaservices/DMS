"""D-2 security fix -- Row-Level Security was enabled and correctly written
across 17 tables (D2_tenant_isolation_security_review.md, Finding 1) but did
nothing, because the application connected as `docsearch`, a genuine
Postgres superuser, and superusers unconditionally bypass RLS. These tests
exercise the real fix: a restricted `dms_app` role (migration 0046,
NOSUPERUSER NOBYPASSRLS) that FastAPI's get_db/get_tenant_db actually
connect as (database.py's AppSessionLocal), with app.current_tenant_id
genuinely set from the caller's own verified JWT -- not the correctly-
written-but-disconnected policies Finding 2 described.

AsyncSessionLocal (the superuser connection) seeds fixtures and reads
pg_roles/pg_policies directly. AppSessionLocal is a SEPARATE physical
connection/pool, so fixtures must be genuinely COMMITTED (not just
flushed) to be visible through it -- and since that commit means a plain
rollback can't clean up afterward, every test explicitly deletes what it
created.
"""
import uuid

import pytest
from sqlalchemy import text, select, func, delete

from app.database import AsyncSessionLocal, AppSessionLocal, set_request_gucs
from app.models.tenant import Tenant
from app.models.user import User, UserRole
from app.models.document import Document
from app.services.auth_service import hash_password


@pytest.mark.asyncio
async def test_dms_app_role_is_genuinely_restricted():
    """Migration 0046's whole point: `dms_app` must NOT be a superuser and
    must NOT have BYPASSRLS -- either one alone makes every policy below a
    no-op, which is exactly Finding 1's bug for the original `docsearch`
    role."""
    async with AsyncSessionLocal() as db:
        res = await db.execute(text(
            "SELECT rolsuper, rolbypassrls, rolcanlogin FROM pg_roles WHERE rolname = 'dms_app'"
        ))
        row = res.one_or_none()
        assert row is not None, "migration 0046 has not run against this database"
        rolsuper, rolbypassrls, rolcanlogin = row
        assert rolsuper is False
        assert rolbypassrls is False
        assert rolcanlogin is True


@pytest.mark.asyncio
async def test_app_session_local_denies_by_default_with_no_tenant_context():
    """The core Finding-1-and-2 fix, live: a connection through
    AppSessionLocal (what get_db/get_tenant_db actually use) with no
    app.current_tenant_id set must see zero rows on an RLS-protected table
    -- fail-safe default-deny, not the superuser's "see everything"."""
    tenant_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(Tenant(id=tenant_id, name=f"RLS Test {uuid.uuid4().hex[:6]}"))
        await db.flush()
        db.add(Document(id=doc_id, tenant_id=tenant_id, title="rls test doc", status="indexed"))
        await db.commit()
    try:
        async with AppSessionLocal() as db:
            res = await db.execute(select(func.count(Document.id)).where(Document.id == doc_id))
            assert res.scalar() == 0
    finally:
        async with AsyncSessionLocal() as db:
            await db.execute(delete(Document).where(Document.id == doc_id))
            await db.execute(delete(Tenant).where(Tenant.id == tenant_id))
            await db.commit()


@pytest.mark.asyncio
async def test_app_session_local_sees_only_the_context_tenant():
    """Same fixture, but with app.current_tenant_id genuinely set to the
    owning tenant -- must see exactly that document, and a DIFFERENT
    tenant's context must still see zero rows for it (real cross-tenant
    isolation, not just "context set == see everything")."""
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    doc_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(Tenant(id=tenant_a, name=f"RLS Test A {uuid.uuid4().hex[:6]}"))
        db.add(Tenant(id=tenant_b, name=f"RLS Test B {uuid.uuid4().hex[:6]}"))
        await db.flush()
        db.add(Document(id=doc_id, tenant_id=tenant_a, title="rls test doc", status="indexed"))
        await db.commit()
    try:
        async with AppSessionLocal() as db:
            await set_request_gucs(db, {"app.current_tenant_id": str(tenant_a), "app.dept_scoped": "0"})
            res = await db.execute(select(Document).where(Document.id == doc_id))
            assert res.scalar_one_or_none() is not None

        async with AppSessionLocal() as db:
            await set_request_gucs(db, {"app.current_tenant_id": str(tenant_b), "app.dept_scoped": "0"})
            res = await db.execute(select(Document).where(Document.id == doc_id))
            assert res.scalar_one_or_none() is None
    finally:
        async with AsyncSessionLocal() as db:
            await db.execute(delete(Document).where(Document.id == doc_id))
            await db.execute(delete(Tenant).where(Tenant.id.in_([tenant_a, tenant_b])))
            await db.commit()


@pytest.mark.asyncio
async def test_login_lookup_email_policy_is_scoped_to_one_email():
    """Finding 3's iam_dg_users fix: a pre-authentication flow (login,
    signup, forgot-password) has no tenant context yet, so it needs the
    second, narrow permissive policy migration 0046 adds -- setting
    app.login_lookup_email must reveal exactly that one user, never a
    different real user, even with zero tenant context set."""
    tenant_id = uuid.uuid4()
    target_email = f"rls_login_target_{uuid.uuid4().hex[:8]}@test.com"
    other_email = f"rls_login_other_{uuid.uuid4().hex[:8]}@test.com"
    user1_id, user2_id = uuid.uuid4(), uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(Tenant(id=tenant_id, name=f"RLS Login Test {uuid.uuid4().hex[:6]}"))
        await db.flush()
        db.add(User(
            id=user1_id, tenant_id=tenant_id, email=target_email,
            hashed_password=hash_password("x"), role=UserRole.it_admin,
        ))
        db.add(User(
            id=user2_id, tenant_id=tenant_id, email=other_email,
            hashed_password=hash_password("x"), role=UserRole.it_admin,
        ))
        await db.commit()
    try:
        async with AppSessionLocal() as db:
            # No tenant context at all -- only the email-lookup policy applies.
            await db.execute(
                text("SELECT set_config('app.login_lookup_email', :e, false)"), {"e": target_email}
            )
            res = await db.execute(select(User).where(User.email == target_email))
            found = res.scalar_one_or_none()
            assert found is not None
            assert found.email == target_email

            # The same session, same (lack of) tenant context, must NOT
            # reveal a different real user just because it's iam_dg_users.
            res2 = await db.execute(select(User).where(User.email == other_email))
            assert res2.scalar_one_or_none() is None
    finally:
        async with AsyncSessionLocal() as db:
            await db.execute(delete(User).where(User.id.in_([user1_id, user2_id])))
            await db.execute(delete(Tenant).where(Tenant.id == tenant_id))
            await db.commit()
