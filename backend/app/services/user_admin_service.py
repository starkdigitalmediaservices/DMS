"""Tenant user management for it_admin: list users, add a user with a
one-time temporary password, change a user's role.

Until this existed, the only way to give anyone a role other than it_admin
was a direct SQL UPDATE -- sign-up always creates a new tenant, and nothing
could add a second person to an existing one.
"""
import secrets
import string
from typing import List, Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.department import Department, DepartmentMember
from app.models.user import User, UserRole
from app.services.audit_service import log_action
from app.services.auth_service import hash_password

# The six personas. The legacy 'admin'/'user' enum values can't be removed
# from Postgres but must never be assigned again.
ASSIGNABLE_ROLES = (
    "records_officer", "operator", "department_head", "legal_counsel", "it_admin", "auditor",
)


def _validate_role(role: str) -> UserRole:
    if role not in ASSIGNABLE_ROLES:
        raise HTTPException(status_code=422, detail=f"role must be one of: {', '.join(ASSIGNABLE_ROLES)}")
    return UserRole(role)


def _temp_password() -> str:
    # Letters, digits and one symbol from each class so it satisfies any
    # reasonable complexity rule; 16 chars from ~70 symbols ≈ 98 bits.
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(16))
        if (any(c.islower() for c in pw) and any(c.isupper() for c in pw)
                and any(c.isdigit() for c in pw) and any(c in "!@#$%^&*" for c in pw)):
            return pw


async def _departments_by_user(db: AsyncSession, tenant_id: UUID) -> dict:
    rows = await db.execute(
        select(DepartmentMember.user_id, Department.id, Department.name)
        .join(Department, Department.id == DepartmentMember.department_id)
        .where(Department.tenant_id == tenant_id)
        .order_by(Department.name)
    )
    out: dict = {}
    for user_id, dept_id, name in rows.all():
        out.setdefault(user_id, []).append({"id": str(dept_id), "name": name})
    return out


def _serialize(user: User, departments: Optional[list] = None) -> dict:
    return {
        "id": str(user.id),
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role.value if hasattr(user.role, "value") else str(user.role),
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "departments": departments or [],
    }


async def list_users(db: AsyncSession, tenant_id: UUID) -> List[dict]:
    users = (await db.execute(
        select(User).where(User.tenant_id == tenant_id).order_by(User.created_at)
    )).scalars().all()
    depts = await _departments_by_user(db, tenant_id)
    return [_serialize(u, depts.get(u.id)) for u in users]


async def create_user(db: AsyncSession, tenant_id: UUID, actor_id: UUID, email: str, full_name: str, role: str) -> dict:
    role_enum = _validate_role(role)
    email = email.strip().lower()
    # Emails are unique across all tenants (uq_iam_dg_users_email), but RLS
    # only shows this tenant's rows -- so a duplicate in another tenant
    # surfaces as an IntegrityError on flush, handled below.
    if (await db.execute(select(User.id).where(func.lower(User.email) == email))).first():
        raise HTTPException(status_code=409, detail="A user with this email already exists")

    temp_password = _temp_password()
    user = User(
        tenant_id=tenant_id, email=email, full_name=full_name.strip(),
        hashed_password=hash_password(temp_password), role=role_enum,
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError as exc:
        if "uq_iam_dg_users_email" not in str(exc):
            raise
        await db.rollback()
        raise HTTPException(status_code=409, detail="A user with this email already exists")

    await log_action(db, actor_id, tenant_id, "user.create", resource_type="user", resource_id=user.id,
                     details={"email": email, "role": role})
    return {**_serialize(user), "temp_password": temp_password}


async def update_user(db: AsyncSession, tenant_id: UUID, actor_id: UUID, user_id: UUID,
                      role: Optional[str] = None, full_name: Optional[str] = None) -> dict:
    user = await db.get(User, user_id)
    if not user or user.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="User not found")

    changes = {}
    if role is not None:
        role_enum = _validate_role(role)
        current = user.role.value if hasattr(user.role, "value") else str(user.role)
        if role_enum.value != current:
            if user_id == actor_id:
                raise HTTPException(status_code=400, detail="You can't change your own role; ask another IT admin")
            if current == "it_admin":
                admins = (await db.execute(
                    select(func.count(User.id)).where(User.tenant_id == tenant_id, User.role == UserRole.it_admin)
                )).scalar() or 0
                if admins <= 1:
                    raise HTTPException(status_code=400, detail="This is the last IT admin; promote someone else first")
            user.role = role_enum
            changes["role"] = {"from": current, "to": role_enum.value}

    if full_name is not None and full_name.strip() != user.full_name:
        changes["full_name"] = {"from": user.full_name, "to": full_name.strip()}
        user.full_name = full_name.strip()

    if changes:
        await db.flush()
        await log_action(db, actor_id, tenant_id, "user.update", resource_type="user", resource_id=user.id, details=changes)

    depts = await _departments_by_user(db, tenant_id)
    return _serialize(user, depts.get(user.id))
