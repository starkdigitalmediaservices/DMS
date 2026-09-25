import uuid

import pytest
from sqlalchemy import delete, select

from app.database import AsyncSessionLocal
from app.models.role import Role
from app.models.subscription import Subscription
from app.models.tenant import Tenant
from app.models.user import User, UserRole
from app.schemas.auth import SignUpRequest
from app.services.auth_service import sign_up


@pytest.mark.asyncio
async def test_sign_up_grants_it_admin_not_the_legacy_admin_role():
    """Regression test for a real bug found live 2026-09-02: sign_up()
    assigned the deprecated UserRole.admin to every new tenant's founding
    user, but every RBAC check in the codebase (templates.py,
    governance.py, department_service.py's TENANT_WIDE_ROLES,
    document_service.py's it_admin fallback, the /admin analytics widget)
    gates on 'it_admin', the persona T50's migration actually moved
    system-level access onto. A fresh signup was silently locked out of
    template management and DMS Analytics with no self-service way to
    fix it -- confirmed live via a real signup showing "This action
    requires one of: it_admin" on its own Admin Panel.

    sign_up() commits internally (creates a tenant + subscription in the
    same transaction), so this can't rely on a rollback like the
    read-only-natured tests elsewhere in this file -- clean up explicitly.
    """
    async with AsyncSessionLocal() as db:
        body = SignUpRequest(
            full_name="Role Regression Test",
            email=f"role_regression_{uuid.uuid4().hex}@test.com",
            password="RegressionTest123!",
        )
        resp = await sign_up(body, db)
        try:
            created = (await db.execute(select(User).where(User.id == resp.user_id))).scalar_one()
            assert created.role == UserRole.it_admin
            assert created.role != UserRole.admin
            # R5: the organisation starts with exactly one role, the locked
            # Admin, and the founding user holds it.
            roles = (await db.execute(select(Role).where(Role.tenant_id == resp.tenant_id))).scalars().all()
            assert len(roles) == 1
            assert roles[0].name == "Admin" and roles[0].is_system and roles[0].all_departments
            assert created.role_id == roles[0].id
        finally:
            await db.execute(delete(User).where(User.id == resp.user_id))
            await db.execute(delete(Role).where(Role.tenant_id == resp.tenant_id))
            await db.execute(delete(Subscription).where(Subscription.tenant_id == resp.tenant_id))
            await db.execute(delete(Tenant).where(Tenant.id == resp.tenant_id))
            await db.commit()
