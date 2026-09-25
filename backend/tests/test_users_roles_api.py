"""Custom roles R7 (docs/features/custom-roles/TASKS.md): assigning roles to
users through /api/v1/users, and every guard on it.

World: one tenant with two Admins, an "HR" delegate (users.manage +
facts.review, not Admin), roles Clerk (facts.review), Big (config.manage)
and Wide (all_departments), plus a second tenant's role.
"""
import uuid

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.main import app
from app.models.audit_log import AuditLog
from app.models.role import Role
from app.models.tenant import Tenant
from app.models.user import User, UserRole
from app.services.auth_service import create_access_token, hash_password


@pytest_asyncio.fixture
async def w():
    t, other = uuid.uuid4(), uuid.uuid4()
    w = {"tenant": t}
    async with AsyncSessionLocal() as db:
        db.add_all([Tenant(id=t, name=f"users-roles-{t.hex[:6]}"), Tenant(id=other, name=f"users-roles-{other.hex[:6]}")])
        await db.flush()
        for key, tenant, kw in [
            ("admin_role", t, dict(name="Admin", is_system=True, all_departments=True, permissions=[])),
            ("hr_role", t, dict(name="HR", permissions=["users.manage", "facts.review"])),
            ("clerk_role", t, dict(name="Clerk", permissions=["facts.review"])),
            ("big_role", t, dict(name="Big", permissions=["config.manage"])),
            ("wide_role", t, dict(name="Wide", all_departments=True, permissions=[])),
            ("foreign_role", other, dict(name="Elsewhere", permissions=[])),
        ]:
            w[key] = uuid.uuid4()
            db.add(Role(id=w[key], tenant_id=tenant, **kw))
        await db.flush()
        for key, role, legacy in [("admin", "admin_role", UserRole.it_admin), ("admin2", "admin_role", UserRole.it_admin),
                                  ("hr", "hr_role", UserRole.operator), ("clerk", "clerk_role", UserRole.operator)]:
            w[key] = uuid.uuid4()
            db.add(User(id=w[key], tenant_id=t, email=f"{key}-{w[key].hex[:8]}@users-roles.com", full_name=key,
                        hashed_password=hash_password("x" * 12), role=legacy, role_id=w[role]))
        await db.commit()
    return w


def _client(w, who):
    token = create_access_token(w[who], w["tenant"], "operator")
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


def _new_email():
    return f"new-{uuid.uuid4().hex[:10]}@users-roles.com"


async def test_create_user_with_role_id(w):
    async with _client(w, "admin") as c:
        r = await c.post("/api/v1/users", json={"email": _new_email(), "full_name": "Asha", "role_id": str(w["clerk_role"])})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["role_id"] == str(w["clerk_role"]) and body["role_name"] == "Clerk" and body["temp_password"]


async def test_create_user_rejects_other_tenants_role(w):
    async with _client(w, "admin") as c:
        r = await c.post("/api/v1/users", json={"email": _new_email(), "full_name": "Asha", "role_id": str(w["foreign_role"])})
    assert r.status_code == 422


async def test_create_user_requires_a_role(w):
    async with _client(w, "admin") as c:
        r = await c.post("/api/v1/users", json={"email": _new_email(), "full_name": "Asha"})
    assert r.status_code == 422


async def test_legacy_persona_still_accepted_until_r11(w):
    async with _client(w, "admin") as c:
        r = await c.post("/api/v1/users", json={"email": _new_email(), "full_name": "Asha", "role": "auditor"})
    assert r.status_code == 201, r.text
    assert r.json()["role_id"] is None and r.json()["role_name"] == "Auditor"


async def test_list_shows_role_names(w):
    async with _client(w, "admin") as c:
        users = {u["full_name"]: u for u in (await c.get("/api/v1/users")).json()}
    assert users["clerk"]["role_name"] == "Clerk" and users["admin"]["is_admin"] is True


async def test_change_role_is_audited(w):
    async with _client(w, "admin") as c:
        r = await c.patch(f"/api/v1/users/{w['clerk']}", json={"role_id": str(w["big_role"])})
    assert r.status_code == 200, r.text
    assert r.json()["role_name"] == "Big"
    async with AsyncSessionLocal() as db:
        log = (await db.execute(select(AuditLog).where(
            AuditLog.action == "user.update", AuditLog.resource_id == w["clerk"]))).scalars().first()
    assert log.details["role"] == {"from": "Clerk", "to": "Big"}


async def test_cannot_change_own_role(w):
    async with _client(w, "admin") as c:
        r = await c.patch(f"/api/v1/users/{w['admin']}", json={"role_id": str(w["clerk_role"])})
    assert r.status_code == 400


async def test_admin_role_changes_need_another_admin(w):
    # The last-Admin guard is defence in depth: through the API only an Admin
    # can change an Admin (D2) and nobody changes their own role, so a
    # tenant always keeps at least one Admin by construction.
    async with _client(w, "admin") as c:
        assert (await c.patch(f"/api/v1/users/{w['admin2']}", json={"role_id": str(w["clerk_role"])})).status_code == 200
    async with _client(w, "admin2") as c:  # admin2 is no longer Admin -> can't manage users at all
        assert (await c.patch(f"/api/v1/users/{w['admin']}", json={"role_id": str(w["clerk_role"])})).status_code == 403
    # Promote HR to Admin, then HR (now Admin) tries to demote the other Admin: allowed, since two exist.
    async with _client(w, "admin") as c:
        assert (await c.patch(f"/api/v1/users/{w['hr']}", json={"role_id": str(w["admin_role"])})).status_code == 200
    async with _client(w, "hr") as c:
        assert (await c.patch(f"/api/v1/users/{w['admin']}", json={"role_id": str(w["clerk_role"])})).status_code == 200
    # Now HR is the only Admin left; another Admin can't exist to demote them, and they can't demote themselves.
    async with _client(w, "hr") as c:
        assert (await c.patch(f"/api/v1/users/{w['hr']}", json={"role_id": str(w["clerk_role"])})).status_code == 400


async def test_last_admin_guard_counts_admins(w):
    from app.services import user_admin_service
    async with AsyncSessionLocal() as db:
        assert await user_admin_service._admin_count(db, w["tenant"]) == 2


# ── delegation guard (D2) ──────────────────────────────────────────────

async def test_delegate_assigns_role_within_own_permissions(w):
    async with _client(w, "hr") as c:
        r = await c.post("/api/v1/users", json={"email": _new_email(), "full_name": "Ravi", "role_id": str(w["clerk_role"])})
    assert r.status_code == 201, r.text


async def test_delegate_cannot_make_an_admin(w):
    async with _client(w, "hr") as c:
        r = await c.patch(f"/api/v1/users/{w['clerk']}", json={"role_id": str(w["admin_role"])})
    assert r.status_code == 403


async def test_delegate_cannot_assign_bigger_or_wide_role(w):
    async with _client(w, "hr") as c:
        assert (await c.patch(f"/api/v1/users/{w['clerk']}", json={"role_id": str(w["big_role"])})).status_code == 403
        assert (await c.patch(f"/api/v1/users/{w['clerk']}", json={"role_id": str(w["wide_role"])})).status_code == 403


async def test_delegate_cannot_demote_an_admin(w):
    async with _client(w, "hr") as c:
        r = await c.patch(f"/api/v1/users/{w['admin2']}", json={"role_id": str(w["clerk_role"])})
    assert r.status_code == 403


async def test_delegate_cannot_use_legacy_it_admin(w):
    async with _client(w, "hr") as c:
        r = await c.post("/api/v1/users", json={"email": _new_email(), "full_name": "Ravi", "role": "it_admin"})
    assert r.status_code == 403


# ── R14: a folder shared with one user ─────────────────────────────────

async def test_folder_shared_with_one_user_only(w):
    from app.models.document import Document
    from app.models.folder import Folder

    t = w["tenant"]
    shared, private = uuid.uuid4(), uuid.uuid4()
    doc_shared, doc_private = uuid.uuid4(), uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add_all([Folder(id=shared, tenant_id=t, name="Audit 2025"), Folder(id=private, tenant_id=t, name="Payroll")])
        await db.flush()
        sub = uuid.uuid4()
        db.add(Folder(id=sub, tenant_id=t, name="Q1", parent_id=shared))
        await db.flush()
        db.add_all([
            Document(id=doc_shared, tenant_id=t, title="q1 audit", status="indexed", folder_id=sub, created_by=w["admin"]),
            Document(id=doc_private, tenant_id=t, title="salaries", status="indexed", folder_id=private, created_by=w["admin"]),
        ])
        await db.commit()

    async def clerk_sees():
        async with _client(w, "clerk") as c:
            r = await c.get("/api/v1/documents", params={"include_all": "true"})
            assert r.status_code == 200, r.text
            return {d["id"] for d in r.json()}

    assert str(doc_shared) not in await clerk_sees()

    async with _client(w, "admin") as c:
        r = await c.post(f"/api/v1/users/{w['clerk']}/folders", json={"folder_id": str(shared)})
        assert r.status_code == 201, r.text
        assert (await c.post(f"/api/v1/users/{w['clerk']}/folders", json={"folder_id": str(shared)})).status_code == 409
        listed = {u["full_name"]: u for u in (await c.get("/api/v1/users")).json()}
        assert listed["clerk"]["folders"] == [{"folder_id": str(shared), "name": "Audit 2025"}]
        assert listed["hr"]["folders"] == []

    seen = await clerk_sees()
    assert str(doc_shared) in seen          # subfolder included
    assert str(doc_private) not in seen      # nothing else opened up

    async with _client(w, "admin") as c:
        assert (await c.delete(f"/api/v1/users/{w['clerk']}/folders/{shared}")).status_code == 204
        assert (await c.delete(f"/api/v1/users/{w['clerk']}/folders/{shared}")).status_code == 404
    assert str(doc_shared) not in await clerk_sees()

    async with AsyncSessionLocal() as db:
        actions = set((await db.execute(select(AuditLog.action).where(AuditLog.resource_id == w["clerk"]))).scalars())
    assert {"user.grant_folder", "user.revoke_folder"} <= actions


async def test_sharing_a_folder_needs_departments_manage(w):
    async with _client(w, "hr") as c:  # users.manage only
        r = await c.post(f"/api/v1/users/{w['clerk']}/folders", json={"folder_id": str(uuid.uuid4())})
    assert r.status_code == 403
