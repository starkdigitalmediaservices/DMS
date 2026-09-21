from typing import Optional, Set
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.department import Department, DepartmentMember, DepartmentFolder
from app.services.audit_service import log_action

# T50 — roles with tenant-wide reach regardless of department membership.
# it_admin (system administration), auditor (must be able to see
# everything to audit it), legal_counsel (legal matters cut across
# departments, not confined to one). The remaining three personas
# (records_officer, operator, department_head) are scoped to whatever
# projects their department has been granted.
TENANT_WIDE_ROLES = {"it_admin", "auditor", "legal_counsel"}
DEPARTMENT_SCOPED_ROLES = {"records_officer", "operator", "department_head"}


async def create_department(db: AsyncSession, tenant_id: UUID, name: str, actor_id: UUID) -> Department:
    if actor_id is None:
        raise ValueError("creating a department requires an actor")

    dept = Department(tenant_id=tenant_id, name=name, created_by_actor_id=actor_id)
    db.add(dept)
    await db.flush()

    await log_action(db, actor_id, tenant_id, "department.create", resource_type="department", resource_id=dept.id, details={"name": name})
    return dept


async def add_department_member(db: AsyncSession, tenant_id: UUID, department_id: UUID, user_id: UUID, actor_id: UUID) -> DepartmentMember:
    if actor_id is None:
        raise ValueError("adding a department member requires an actor")

    dept = await db.get(Department, department_id)
    if not dept or dept.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Department not found")

    existing = await db.execute(
        select(DepartmentMember).where(DepartmentMember.department_id == department_id, DepartmentMember.user_id == user_id)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="User is already a member of this department")

    member = DepartmentMember(tenant_id=tenant_id, department_id=department_id, user_id=user_id)
    db.add(member)
    await db.flush()

    await log_action(db, actor_id, tenant_id, "department.add_member", resource_type="department", resource_id=department_id, details={"user_id": str(user_id)})
    return member


async def grant_department_folder(db: AsyncSession, tenant_id: UUID, department_id: UUID, folder_id: UUID, actor_id: UUID) -> DepartmentFolder:
    """Grant a department scope over one project (folder) — independent
    of where that folder sits in the tree."""
    if actor_id is None:
        raise ValueError("granting folder scope requires an actor")

    dept = await db.get(Department, department_id)
    if not dept or dept.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Department not found")

    existing = await db.execute(
        select(DepartmentFolder).where(DepartmentFolder.department_id == department_id, DepartmentFolder.folder_id == folder_id)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="This folder is already granted to this department")

    grant = DepartmentFolder(tenant_id=tenant_id, department_id=department_id, folder_id=folder_id)
    db.add(grant)
    await db.flush()

    await log_action(db, actor_id, tenant_id, "department.grant_folder", resource_type="department", resource_id=department_id, details={"folder_id": str(folder_id)})
    return grant


async def delete_department(db: AsyncSession, tenant_id: UUID, department_id: UUID, actor_id: UUID) -> None:
    """Delete a department along with its memberships and folder grants.

    The child rows are removed explicitly because neither FK declares a
    cascade — without this the delete just fails on a referencing row.

    Worth being deliberate about: a department is an access-control
    boundary (see user_has_folder_scope), so removing one REVOKES folder
    scope for every department-scoped member that was relying on it. That
    is the intended effect, not a side effect, which is why the audit
    entry records how much was revoked rather than just the name.
    """
    if actor_id is None:
        raise ValueError("deleting a department requires an actor")

    dept = await db.get(Department, department_id)
    if not dept or dept.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Department not found")

    member_ids = (await db.execute(
        select(DepartmentMember.id).where(DepartmentMember.department_id == department_id)
    )).scalars().all()
    folder_ids = (await db.execute(
        select(DepartmentFolder.folder_id).where(DepartmentFolder.department_id == department_id)
    )).scalars().all()

    await db.execute(delete(DepartmentFolder).where(DepartmentFolder.department_id == department_id))
    await db.execute(delete(DepartmentMember).where(DepartmentMember.department_id == department_id))
    await db.delete(dept)
    await db.flush()

    await log_action(
        db, actor_id, tenant_id, "department.delete",
        resource_type="department", resource_id=department_id,
        details={
            "name": dept.name,
            "members_removed": len(member_ids),
            "folder_grants_revoked": len(folder_ids),
        },
    )


async def list_user_department_folder_ids(db: AsyncSession, tenant_id: UUID, user_id: UUID) -> Set[UUID]:
    """Every folder granted to any department this user belongs to."""
    stmt = (
        select(DepartmentFolder.folder_id)
        .join(DepartmentMember, DepartmentMember.department_id == DepartmentFolder.department_id)
        .where(DepartmentMember.user_id == user_id, DepartmentFolder.tenant_id == tenant_id)
    )
    res = await db.execute(stmt)
    return set(res.scalars().all())


async def user_has_folder_scope(db: AsyncSession, tenant_id: UUID, user_id: UUID, role: str, folder_id: Optional[UUID]) -> bool:
    """Does this user have access to this folder/project?

    Tenant-wide roles always do. Department-scoped roles only do if the
    folder was explicitly granted to a department they belong to.
    folder_id=None (root/no folder) is always visible — there's nothing
    to scope against.
    """
    if role in TENANT_WIDE_ROLES:
        return True
    if folder_id is None:
        return True

    granted = await list_user_department_folder_ids(db, tenant_id, user_id)
    return folder_id in granted
