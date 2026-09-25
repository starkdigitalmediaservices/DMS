"""Custom roles R2 (docs/features/custom-roles/TASKS.md): the permission
catalogue, the grant rule, the live role lookup and require_permission.

Fixtures are committed through the superuser connection, because
load_live_access reads through the separate restricted pool; conftest's
session purge removes the tenants afterwards.
"""
import uuid

import pytest
import pytest_asyncio
from fastapi import HTTPException

from app.database import AsyncSessionLocal
from app.deps import load_live_access, require_permission
from app.models.role import Role
from app.models.tenant import Tenant
from app.models.user import User, UserRole
from app.permissions import PERMISSION_KEYS, PERMISSIONS, ROLE_TEMPLATES, check_key, grants, legacy_access, role_grants
from app.schemas.auth import TokenPayload
from app.services.auth_service import hash_password


# ── catalogue ───────────────────────────────────────────────────────────

def test_catalogue_keys_are_unique_and_grouped():
    assert len(PERMISSION_KEYS) == len(PERMISSIONS) == 21
    assert {p.group for p in PERMISSIONS} == {"Review", "Documents", "Reports", "Administration"}
    assert "roles.manage" in PERMISSION_KEYS


def test_templates_only_use_known_keys_and_have_no_admin():
    for t in ROLE_TEMPLATES.values():
        assert set(t.permissions) <= PERMISSION_KEYS
    assert "it_admin" not in ROLE_TEMPLATES  # the locked Admin role replaces it


def test_check_key_rejects_typos():
    assert check_key("facts.review") == "facts.review"
    with pytest.raises(ValueError):
        check_key("facts.reveiw")


def test_require_permission_rejects_unknown_key_at_definition():
    with pytest.raises(ValueError):
        require_permission("no.such.permission")


# ── grant rule ──────────────────────────────────────────────────────────

def test_admin_holds_everything_even_with_empty_list():
    assert all(role_grants(True, [], k) for k in PERMISSION_KEYS)


def test_custom_role_holds_exactly_what_is_ticked():
    assert role_grants(False, ["facts.review"], "facts.review")
    assert not role_grants(False, ["facts.review"], "users.manage")


def test_no_role_holds_nothing():
    assert not any(role_grants(False, None, k) for k in PERMISSION_KEYS)


# ── require_permission ─────────────────────────────────────────────────

def _payload(**kw):
    base = dict(sub=str(uuid.uuid4()), tenant_id=str(uuid.uuid4()), role="operator",
                exp=10_000_000_000, jti=str(uuid.uuid4()))
    return TokenPayload(**{**base, **kw})


async def test_require_permission_allows_granted_key():
    user = _payload(permissions=["facts.review"])
    assert await require_permission("facts.review")(current_user=user) is user


async def test_require_permission_denies_missing_key():
    with pytest.raises(HTTPException) as e:
        await require_permission("users.manage")(current_user=_payload(permissions=["facts.review"]))
    assert e.value.status_code == 403


async def test_require_permission_admin_passes_any_key():
    user = _payload(is_admin=True)
    assert await require_permission("config.manage")(current_user=user) is user


async def test_token_payload_defaults_to_no_access():
    p = _payload()
    assert p.permissions == [] and p.is_admin is False and p.all_departments is False


# ── live lookup against the database ───────────────────────────────────

@pytest_asyncio.fixture
async def world():
    t, other = uuid.uuid4(), uuid.uuid4()
    w = {"tenant": t, "other": other}
    async with AsyncSessionLocal() as db:
        db.add_all([Tenant(id=t, name=f"roles-test-{t.hex[:6]}"),
                    Tenant(id=other, name=f"roles-test-{other.hex[:6]}")])
        await db.flush()
        w["admin_role"], w["clerk_role"], w["foreign_role"] = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        db.add_all([
            Role(id=w["admin_role"], tenant_id=t, name="Admin", is_system=True, all_departments=True, permissions=[]),
            Role(id=w["clerk_role"], tenant_id=t, name="Clerk", permissions=["facts.review"]),
            Role(id=w["foreign_role"], tenant_id=other, name="Admin", is_system=True, all_departments=True, permissions=[]),
        ])
        await db.flush()
        for key, role_id in [("admin", w["admin_role"]), ("clerk", w["clerk_role"]),
                             ("norole", None), ("crossed", w["foreign_role"])]:
            uid = uuid.uuid4()
            w[key] = uid
            db.add(User(id=uid, tenant_id=t, email=f"{key}-{uid.hex[:8]}@roles-test.com", full_name=key,
                        hashed_password=hash_password("x" * 12), role=UserRole.operator, role_id=role_id))
        await db.commit()
    return w


async def test_live_access_admin(world):
    a = await load_live_access(str(world["admin"]), str(world["tenant"]))
    assert a["is_admin"] and a["all_departments"] and a["role_name"] == "Admin"


async def test_live_access_custom_role(world):
    a = await load_live_access(str(world["clerk"]), str(world["tenant"]))
    assert a["permissions"] == ["facts.review"] and not a["is_admin"] and not a["all_departments"]


async def test_live_access_without_role_id_falls_back_to_old_persona(world):
    # Bridge until R13: no role_id -> exactly the old persona's access.
    a = await load_live_access(str(world["norole"]), str(world["tenant"]))
    assert a["role_id"] is None and not a["is_admin"]
    assert set(a["permissions"]) == set(ROLE_TEMPLATES["operator"].permissions)


async def test_live_access_ignores_role_from_another_tenant(world):
    # The FK alone doesn't stop role_id pointing across tenants; the lookup
    # must -- the foreign Admin role is ignored, not honoured.
    a = await load_live_access(str(world["crossed"]), str(world["tenant"]))
    assert a["role_id"] is None and not a["is_admin"]


async def test_live_access_sees_role_change_on_next_call(world):
    async with AsyncSessionLocal() as db:
        role = await db.get(Role, world["clerk_role"])
        role.permissions = ["facts.review", "users.manage"]
        await db.commit()
    a = await load_live_access(str(world["clerk"]), str(world["tenant"]))
    assert "users.manage" in a["permissions"]


async def test_live_access_wrong_tenant_is_none(world):
    assert await load_live_access(str(world["clerk"]), str(world["other"])) is None


# ── legacy bridge ───────────────────────────────────────────────────────

def test_legacy_access_matches_old_gates():
    assert legacy_access("it_admin")[0] is True
    assert legacy_access("admin")[0] is True
    assert legacy_access("user") == legacy_access("operator")
    assert legacy_access("auditor")[1] is True           # all departments
    assert legacy_access("records_officer")[1] is False
    assert legacy_access("nonsense") == (False, False, [], None)


def test_grants_accepts_persona_string_or_payload():
    assert grants("records_officer", "review.verify")
    assert not grants("operator", "review.verify")
    assert grants(_payload(permissions=["review.verify"]), "review.verify")
    assert not grants(_payload(), "review.verify")


# ── end to end: a custom role gates a real endpoint ────────────────────

async def test_custom_role_gates_real_endpoint_and_change_applies_next_request(world):
    from httpx import ASGITransport, AsyncClient
    from app.main import app
    from app.services.auth_service import create_access_token

    # The token still says "operator"; access must come from the live role.
    token = create_access_token(world["clerk"], world["tenant"], "operator")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           headers={"Authorization": f"Bearer {token}"}) as c:
        r = await c.get("/api/v1/users")
        assert r.status_code == 403, r.text

        async with AsyncSessionLocal() as db:
            role = await db.get(Role, world["clerk_role"])
            role.permissions = ["users.manage"]
            await db.commit()

        r = await c.get("/api/v1/users")
        assert r.status_code == 200, r.text

        async with AsyncSessionLocal() as db:
            role = await db.get(Role, world["clerk_role"])
            role.permissions = []
            await db.commit()

        assert (await c.get("/api/v1/users")).status_code == 403


# ── R4: department scope follows the role's all_departments flag ───────

async def test_all_departments_flag_lifts_scope(world):
    from httpx import ASGITransport, AsyncClient
    from app.main import app
    from app.models.department import Department, DepartmentFolder, DepartmentMember
    from app.models.document import Document
    from app.models.folder import Folder
    from app.services.auth_service import create_access_token

    t = world["tenant"]
    granted, other = uuid.uuid4(), uuid.uuid4()
    doc_granted, doc_other = uuid.uuid4(), uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add_all([Folder(id=granted, tenant_id=t, name="Billing"), Folder(id=other, tenant_id=t, name="Legal")])
        await db.flush()
        db.add_all([
            Document(id=doc_granted, tenant_id=t, title="bill", status="indexed", folder_id=granted, created_by=world["admin"]),
            Document(id=doc_other, tenant_id=t, title="case", status="indexed", folder_id=other, created_by=world["admin"]),
        ])
        dept = Department(tenant_id=t, name="Accounts", created_by_actor_id=world["admin"])
        db.add(dept)
        await db.flush()
        db.add_all([DepartmentMember(tenant_id=t, department_id=dept.id, user_id=world["clerk"]),
                    DepartmentFolder(tenant_id=t, department_id=dept.id, folder_id=granted)])
        await db.commit()

    token = create_access_token(world["clerk"], t, "operator")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           headers={"Authorization": f"Bearer {token}"}) as c:
        async def visible():
            r = await c.get("/api/v1/documents", params={"include_all": "true"})
            assert r.status_code == 200, r.text
            return {d["id"] for d in r.json()}

        seen = await visible()
        assert str(doc_granted) in seen and str(doc_other) not in seen

        async with AsyncSessionLocal() as db:
            (await db.get(Role, world["clerk_role"])).all_departments = True
            await db.commit()
        seen = await visible()
        assert {str(doc_granted), str(doc_other)} <= seen

        async with AsyncSessionLocal() as db:
            (await db.get(Role, world["clerk_role"])).all_departments = False
            await db.commit()
        assert str(doc_other) not in await visible()


# ── R8: /auth/me tells the UI what the caller may do ───────────────────

async def test_auth_me_returns_live_permissions(world):
    from httpx import ASGITransport, AsyncClient
    from app.main import app
    from app.services.auth_service import create_access_token

    async def me(who):
        token = create_access_token(world[who], world["tenant"], "operator")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                               headers={"Authorization": f"Bearer {token}"}) as c:
            r = await c.get("/api/v1/auth/me")
            assert r.status_code == 200, r.text
            return r.json()

    clerk = await me("clerk")
    assert clerk["role_name"] == "Clerk" and clerk["permissions"] == ["facts.review"]
    assert clerk["is_admin"] is False and clerk["all_departments"] is False

    admin = await me("admin")
    assert admin["is_admin"] is True and set(admin["permissions"]) == PERMISSION_KEYS
