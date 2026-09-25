"""Custom roles R6 (docs/features/custom-roles/TASKS.md): the Roles API and
every guard in role_service, through the real HTTP stack.

World: one tenant with the locked Admin role, a delegated "Head" role
(roles.manage + users.manage + facts.review, NOT Admin), a "Viewer" role
with no permissions, and a second tenant whose role must stay invisible.
Committed via the superuser connection; conftest's purge cleans up.
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
    w = {"tenant": t, "other": other}
    async with AsyncSessionLocal() as db:
        db.add_all([Tenant(id=t, name=f"roles-api-{t.hex[:6]}"), Tenant(id=other, name=f"roles-api-{other.hex[:6]}")])
        await db.flush()
        for key, tenant, kw in [
            ("admin_role", t, dict(name="Admin", is_system=True, all_departments=True, permissions=[])),
            ("head_role", t, dict(name="Head", permissions=["roles.manage", "users.manage", "facts.review"])),
            ("viewer_role", t, dict(name="Viewer", permissions=[])),
            ("hr_role", t, dict(name="HR", permissions=["users.manage"])),
            ("big_role", t, dict(name="Big", permissions=["config.manage"])),
            ("foreign_role", other, dict(name="Elsewhere", permissions=[])),
        ]:
            w[key] = uuid.uuid4()
            db.add(Role(id=w[key], tenant_id=tenant, **kw))
        await db.flush()
        for key, role in [("admin", "admin_role"), ("head", "head_role"), ("viewer", "viewer_role"), ("hr", "hr_role")]:
            w[key] = uuid.uuid4()
            db.add(User(id=w[key], tenant_id=t, email=f"{key}-{w[key].hex[:8]}@roles-api.com", full_name=key,
                        hashed_password=hash_password("x" * 12), role=UserRole.operator, role_id=w[role]))
        await db.commit()
    return w


def _client(w, who):
    token = create_access_token(w[who], w["tenant"], "operator")
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


async def test_list_puts_locked_admin_first_with_counts(w):
    async with _client(w, "admin") as c:
        r = await c.get("/api/v1/roles")
    assert r.status_code == 200, r.text
    roles = r.json()
    assert roles[0]["name"] == "Admin" and roles[0]["is_system"] and roles[0]["user_count"] == 1
    assert "Elsewhere" not in {x["name"] for x in roles}  # other tenant's role


async def test_catalogue_and_templates(w):
    async with _client(w, "admin") as c:
        cat = (await c.get("/api/v1/roles/permissions")).json()
        tpl = (await c.get("/api/v1/roles/templates")).json()
    assert [g["group"] for g in cat] == ["Review", "Documents", "Reports", "Administration"]
    assert {t["name"] for t in tpl} == {"Records Officer", "Operator", "Department Head", "Legal Counsel", "Auditor"}


async def test_admin_creates_role_and_it_is_audited(w):
    async with _client(w, "admin") as c:
        r = await c.post("/api/v1/roles", json={"name": "Accounts Clerk", "permissions": ["facts.review"]})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["permissions"] == ["facts.review"] and body["all_departments"] is False
    async with AsyncSessionLocal() as db:
        log = (await db.execute(select(AuditLog).where(
            AuditLog.action == "role.create", AuditLog.resource_id == uuid.UUID(body["id"])))).scalar_one()
    assert log.details["name"] == "Accounts Clerk"


async def test_duplicate_name_is_case_insensitive(w):
    async with _client(w, "admin") as c:
        r = await c.post("/api/v1/roles", json={"name": "viewer", "permissions": []})
    assert r.status_code == 409


async def test_unknown_permission_rejected(w):
    async with _client(w, "admin") as c:
        r = await c.post("/api/v1/roles", json={"name": "X", "permissions": ["launch.missiles"]})
    assert r.status_code == 422


async def test_admin_role_is_locked(w):
    async with _client(w, "admin") as c:
        assert (await c.patch(f"/api/v1/roles/{w['admin_role']}", json={"name": "Boss"})).status_code == 409
        assert (await c.delete(f"/api/v1/roles/{w['admin_role']}")).status_code == 409


async def test_role_in_use_cannot_be_deleted_unused_can(w):
    async with _client(w, "admin") as c:
        r = await c.delete(f"/api/v1/roles/{w['viewer_role']}")
        assert r.status_code == 409 and "1 user" in r.json()["detail"]
        assert (await c.delete(f"/api/v1/roles/{w['big_role']}")).status_code == 204


async def test_other_tenants_role_is_not_found(w):
    async with _client(w, "admin") as c:
        assert (await c.patch(f"/api/v1/roles/{w['foreign_role']}", json={"name": "Mine"})).status_code == 404


async def test_admin_edits_permissions_and_scope(w):
    async with _client(w, "admin") as c:
        r = await c.patch(f"/api/v1/roles/{w['viewer_role']}",
                          json={"permissions": ["export.report"], "all_departments": True})
    assert r.status_code == 200, r.text
    assert r.json()["permissions"] == ["export.report"] and r.json()["all_departments"] is True


# ── delegation guard (D2) ──────────────────────────────────────────────

async def test_delegate_can_create_role_within_own_permissions(w):
    async with _client(w, "head") as c:
        r = await c.post("/api/v1/roles", json={"name": "Clerk", "permissions": ["facts.review"]})
    assert r.status_code == 201, r.text


async def test_delegate_cannot_grant_what_they_lack(w):
    async with _client(w, "head") as c:
        r = await c.post("/api/v1/roles", json={"name": "Clerk", "permissions": ["config.manage"]})
    assert r.status_code == 403 and "config.manage" in r.json()["detail"]


async def test_delegate_cannot_grant_all_departments(w):
    async with _client(w, "head") as c:
        r = await c.post("/api/v1/roles", json={"name": "Clerk", "permissions": [], "all_departments": True})
    assert r.status_code == 403


async def test_delegate_cannot_touch_a_bigger_role(w):
    async with _client(w, "head") as c:
        assert (await c.patch(f"/api/v1/roles/{w['big_role']}", json={"permissions": []})).status_code == 403
        assert (await c.delete(f"/api/v1/roles/{w['big_role']}")).status_code == 403


# ── who may call it ────────────────────────────────────────────────────

async def test_no_permission_cannot_read_roles(w):
    async with _client(w, "viewer") as c:
        assert (await c.get("/api/v1/roles")).status_code == 403


async def test_users_manage_can_read_but_not_change_roles(w):
    async with _client(w, "hr") as c:
        assert (await c.get("/api/v1/roles")).status_code == 200
        assert (await c.post("/api/v1/roles", json={"name": "Y", "permissions": []})).status_code == 403
