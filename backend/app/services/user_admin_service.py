"""Tenant user management: list users, add a user with a one-time
temporary password, change a user's role.

Custom roles (R7): users are assigned a tenant role by `role_id`. The old
persona string (`role`) is still accepted from the pre-R11 Users screen; it
sets the legacy column and clears role_id, so permissions.legacy_access
applies exactly as before. Guards: role must belong to this tenant; the
last Admin can't be moved; nobody changes their own role; a caller who
isn't Admin can only assign roles within their own permissions (D2).

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
from app.models.role import Role
from app.models.user import User, UserRole
from app.schemas.auth import TokenPayload
from app.services import department_service
from app.services.audit_service import log_action
from app.services.auth_service import hash_password
from app.permissions import legacy_access

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


def _legacy(user: User) -> str:
    return user.role.value if hasattr(user.role, "value") else str(user.role)


def _serialize(user: User, departments: Optional[list] = None, role: Optional[Role] = None,
               folders: Optional[list] = None) -> dict:
    legacy_name = legacy_access(_legacy(user))[3]
    return {
        "id": str(user.id),
        "email": user.email,
        "full_name": user.full_name,
        "role": _legacy(user),
        "role_id": str(role.id) if role else None,
        "role_name": role.name if role else legacy_name,
        "is_admin": bool(role.is_system) if role else _legacy(user) in ("it_admin", "admin"),
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "departments": departments or [],
        "folders": folders or [],  # shared with this user directly (R14)
    }


async def _roles_by_id(db: AsyncSession, tenant_id: UUID) -> dict:
    return {r.id: r for r in (await db.execute(select(Role).where(Role.tenant_id == tenant_id))).scalars().all()}


async def list_users(db: AsyncSession, tenant_id: UUID) -> List[dict]:
    users = (await db.execute(
        select(User).where(User.tenant_id == tenant_id).order_by(User.created_at)
    )).scalars().all()
    depts = await _departments_by_user(db, tenant_id)
    roles = await _roles_by_id(db, tenant_id)
    folders = await department_service.user_folders_by_user(db, tenant_id)
    return [_serialize(u, depts.get(u.id), roles.get(u.role_id), folders.get(u.id)) for u in users]


def _is_admin_holder(user: User, role: Optional[Role]) -> bool:
    return bool(role.is_system) if role else _legacy(user) in ("it_admin", "admin")


async def _admin_count(db: AsyncSession, tenant_id: UUID) -> int:
    return (await db.execute(
        select(func.count(User.id)).select_from(User).outerjoin(Role, Role.id == User.role_id)
        .where(User.tenant_id == tenant_id,
               (Role.is_system.is_(True)) | ((User.role_id.is_(None)) & (User.role == UserRole.it_admin)))
    )).scalar() or 0


async def _resolve_role(db: AsyncSession, tenant_id: UUID, role_id: UUID) -> Role:
    role = await db.get(Role, role_id)
    if not role or role.tenant_id != tenant_id:
        raise HTTPException(status_code=422, detail="Unknown role")
    return role


def _guard_assign(actor: Optional[TokenPayload], role: Optional[Role], legacy: Optional[str] = None) -> None:
    """D2: only an Admin may hand out Admin, all departments, or anything
    beyond their own permissions. actor=None = trusted internal caller."""
    if actor is None or actor.is_admin:
        return
    if role is not None:
        is_admin, all_depts, perms = role.is_system, role.all_departments, list(role.permissions or [])
    else:
        is_admin, all_depts, perms, _ = legacy_access(legacy)
    if is_admin:
        raise HTTPException(status_code=403, detail="Only an Admin can give someone the Admin role")
    if all_depts:
        raise HTTPException(status_code=403, detail="Only an Admin can assign a role with access to all departments")
    beyond = sorted(set(perms) - set(actor.permissions))
    if beyond:
        raise HTTPException(status_code=403, detail=f"You can't assign a role with permissions you don't have: {', '.join(beyond)}")


async def create_user(db: AsyncSession, tenant_id: UUID, actor_id: UUID, email: str, full_name: str,
                      role: Optional[str] = None, role_id: Optional[UUID] = None,
                      actor: Optional[TokenPayload] = None) -> dict:
    if role_id is None and role is None:
        raise HTTPException(status_code=422, detail="role_id is required")
    new_role = await _resolve_role(db, tenant_id, role_id) if role_id is not None else None
    # Legacy column: kept meaningful for the pre-R9 frontend.
    role_enum = (UserRole.it_admin if new_role.is_system else UserRole.operator) if new_role else _validate_role(role)
    _guard_assign(actor, new_role, None if new_role else role)
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
        role_id=new_role.id if new_role else None,
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
                     details={"email": email, "role": new_role.name if new_role else role,
                              "role_id": str(new_role.id) if new_role else None})
    return {**_serialize(user, role=new_role), "temp_password": temp_password}


async def update_user(db: AsyncSession, tenant_id: UUID, actor_id: UUID, user_id: UUID,
                      role: Optional[str] = None, full_name: Optional[str] = None,
                      role_id: Optional[UUID] = None, actor: Optional[TokenPayload] = None) -> dict:
    user = await db.get(User, user_id)
    if not user or user.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="User not found")
    roles = await _roles_by_id(db, tenant_id)
    current_role = roles.get(user.role_id)

    changes = {}
    if role_id is not None or role is not None:
        if role_id is not None:
            new_role = await _resolve_role(db, tenant_id, role_id)
            new_legacy = UserRole.it_admin if new_role.is_system else (
                user.role if _legacy(user) not in ("it_admin", "admin") else UserRole.operator)
            same = user.role_id == new_role.id
        else:
            new_role = None
            new_legacy = _validate_role(role)
            same = user.role_id is None and new_legacy.value == _legacy(user)
        if not same:
            if user_id == actor_id:
                raise HTTPException(status_code=400, detail="You can't change your own role; ask another Admin")
            # The caller must be able to hand out both the old role and the new one.
            _guard_assign(actor, current_role, None if current_role else _legacy(user))
            _guard_assign(actor, new_role, None if new_role else new_legacy.value)
            leaving_admin = _is_admin_holder(user, current_role) and not (
                new_role.is_system if new_role else new_legacy == UserRole.it_admin)
            if leaving_admin and await _admin_count(db, tenant_id) <= 1:
                raise HTTPException(status_code=400, detail="This is the last Admin; give someone else the Admin role first")
            changes["role"] = {
                "from": current_role.name if current_role else _legacy(user),
                "to": new_role.name if new_role else new_legacy.value,
            }
            user.role_id = new_role.id if new_role else None
            user.role = new_legacy
            current_role = new_role

    if full_name is not None and full_name.strip() != user.full_name:
        changes["full_name"] = {"from": user.full_name, "to": full_name.strip()}
        user.full_name = full_name.strip()

    if changes:
        await db.flush()
        await log_action(db, actor_id, tenant_id, "user.update", resource_type="user", resource_id=user.id, details=changes)

    depts = await _departments_by_user(db, tenant_id)
    return _serialize(user, depts.get(user.id), current_role)
