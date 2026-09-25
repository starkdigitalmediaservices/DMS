from typing import Any, Optional, Set
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import REQUEST_GUCS_KEY, set_request_gucs
from app.models.department import Department, DepartmentMember, DepartmentFolder, UserFolder
from app.models.folder import Folder
from app.models.role import Role
from app.permissions import legacy_access, sees_all_departments
from app.models.user import User
from app.services.audit_service import log_action

# T50 — roles with tenant-wide reach regardless of department membership.
# it_admin (system administration), auditor (must be able to see
# everything to audit it), legal_counsel (legal matters cut across
# departments, not confined to one). The remaining three personas
# (records_officer, operator, department_head) are scoped to whatever
# projects their department has been granted.
#
# Enforced by Postgres RLS (migration 0053), not by per-query filters: see
# apply_request_scope. Any role not listed as tenant-wide -- including the
# legacy 'user' value -- is scoped, so an unexpected role fails closed.
#
# Custom roles (2026-09-25): "tenant-wide" is now the role's all_departments
# flag, read via permissions.sees_all_departments. The old personas map onto
# it exactly (it_admin, auditor, legal_counsel = all departments).


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
    user = await db.get(User, user_id)
    if not user or user.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="User not found")

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
    folder = await db.get(Folder, folder_id)
    if not folder or folder.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Folder not found")

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


async def grant_user_folder(db: AsyncSession, tenant_id: UUID, user_id: UUID, folder_id: UUID, actor_id: UUID) -> UserFolder:
    """R14: share one folder (and its subfolders) with one user directly."""
    if actor_id is None:
        raise ValueError("granting folder scope requires an actor")
    user = await db.get(User, user_id)
    if not user or user.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="User not found")
    folder = await db.get(Folder, folder_id)
    if not folder or folder.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Folder not found")
    existing = await db.execute(
        select(UserFolder.id).where(UserFolder.user_id == user_id, UserFolder.folder_id == folder_id)
    )
    if existing.first():
        raise HTTPException(status_code=409, detail="This folder is already shared with this user")
    grant = UserFolder(tenant_id=tenant_id, user_id=user_id, folder_id=folder_id, created_by_actor_id=actor_id)
    db.add(grant)
    await db.flush()
    await log_action(db, actor_id, tenant_id, "user.grant_folder", resource_type="user", resource_id=user_id,
                     details={"folder_id": str(folder_id)})
    return grant


async def revoke_user_folder(db: AsyncSession, tenant_id: UUID, user_id: UUID, folder_id: UUID, actor_id: UUID) -> None:
    res = await db.execute(
        delete(UserFolder)
        .where(UserFolder.tenant_id == tenant_id, UserFolder.user_id == user_id, UserFolder.folder_id == folder_id)
        .returning(UserFolder.id)
    )
    if not res.first():
        raise HTTPException(status_code=404, detail="This folder isn't shared with this user")
    await log_action(db, actor_id, tenant_id, "user.revoke_folder", resource_type="user", resource_id=user_id,
                     details={"folder_id": str(folder_id)})


async def user_folders_by_user(db: AsyncSession, tenant_id: UUID) -> dict:
    rows = await db.execute(
        select(UserFolder.user_id, Folder.id, Folder.name)
        .join(Folder, Folder.id == UserFolder.folder_id)
        .where(UserFolder.tenant_id == tenant_id)
        .order_by(Folder.name)
    )
    out: dict = {}
    for user_id, fid, name in rows.all():
        out.setdefault(user_id, []).append({"folder_id": str(fid), "name": name})
    return out


async def list_departments(db: AsyncSession, tenant_id: UUID) -> list:
    depts = (await db.execute(
        select(Department).where(Department.tenant_id == tenant_id).order_by(Department.name)
    )).scalars().all()
    members = (await db.execute(
        select(DepartmentMember.department_id, User.id, User.email, User.full_name, User.role, Role.name)
        .join(User, User.id == DepartmentMember.user_id)
        .outerjoin(Role, Role.id == User.role_id)
        .where(DepartmentMember.tenant_id == tenant_id)
        .order_by(User.email)
    )).all()
    folders = (await db.execute(
        select(DepartmentFolder.department_id, Folder.id, Folder.name)
        .join(Folder, Folder.id == DepartmentFolder.folder_id)
        .where(DepartmentFolder.tenant_id == tenant_id)
        .order_by(Folder.name)
    )).all()

    by_dept = {d.id: {"id": str(d.id), "name": d.name,
                      "created_at": d.created_at.isoformat() if d.created_at else None,
                      "members": [], "folders": []} for d in depts}
    for dept_id, uid, email, full_name, role, role_name in members:
        if dept_id in by_dept:
            legacy = role.value if hasattr(role, "value") else str(role)
            by_dept[dept_id]["members"].append({
                "user_id": str(uid), "email": email, "full_name": full_name,
                "role": legacy,
                "role_name": role_name or legacy_access(legacy)[3],
            })
    for dept_id, fid, name in folders:
        if dept_id in by_dept:
            by_dept[dept_id]["folders"].append({"folder_id": str(fid), "name": name})
    return list(by_dept.values())


async def remove_department_member(db: AsyncSession, tenant_id: UUID, department_id: UUID, user_id: UUID, actor_id: UUID) -> None:
    res = await db.execute(
        delete(DepartmentMember)
        .where(DepartmentMember.tenant_id == tenant_id, DepartmentMember.department_id == department_id,
               DepartmentMember.user_id == user_id)
        .returning(DepartmentMember.id)
    )
    if not res.first():
        raise HTTPException(status_code=404, detail="User is not a member of this department")
    await log_action(db, actor_id, tenant_id, "department.remove_member", resource_type="department",
                     resource_id=department_id, details={"user_id": str(user_id)})


async def revoke_department_folder(db: AsyncSession, tenant_id: UUID, department_id: UUID, folder_id: UUID, actor_id: UUID) -> None:
    res = await db.execute(
        delete(DepartmentFolder)
        .where(DepartmentFolder.tenant_id == tenant_id, DepartmentFolder.department_id == department_id,
               DepartmentFolder.folder_id == folder_id)
        .returning(DepartmentFolder.id)
    )
    if not res.first():
        raise HTTPException(status_code=404, detail="This folder isn't granted to this department")
    await log_action(db, actor_id, tenant_id, "department.revoke_folder", resource_type="department",
                     resource_id=department_id, details={"folder_id": str(folder_id)})


async def delete_department(db: AsyncSession, tenant_id: UUID, department_id: UUID, actor_id: UUID) -> None:
    """Delete a department along with its memberships and folder grants.

    The child rows are removed explicitly because neither FK declares a
    cascade — without this the delete just fails on a referencing row.

    Worth being deliberate about: a department is an access-control
    boundary (see apply_request_scope), so removing one REVOKES folder
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


async def list_user_scope_folder_ids(db: AsyncSession, tenant_id: UUID, user_id: UUID) -> Set[UUID]:
    """Granted folders plus every folder beneath them. A grant covers the
    whole project subtree, so a document filed in a subfolder of a granted
    project is in scope too. Grants come from the user's departments and,
    since R14, from folders shared with the user directly. Needs
    tenant-wide folder visibility to walk the tree (see apply_request_scope)."""
    res = await db.execute(text("""
        WITH RECURSIVE scope AS (
            SELECT df.folder_id AS id
            FROM iam_dg_department_folders df
            JOIN iam_dg_department_members dm ON dm.department_id = df.department_id
            WHERE dm.user_id = :user_id AND df.tenant_id = :tenant_id
            UNION
            SELECT uf.folder_id FROM iam_dg_user_folders uf
            WHERE uf.user_id = :user_id AND uf.tenant_id = :tenant_id
            UNION
            SELECT f.id FROM doc_dg_folders f JOIN scope s ON f.parent_id = s.id
        )
        SELECT id FROM scope
    """), {"user_id": user_id, "tenant_id": tenant_id})
    return set(res.scalars().all())


async def apply_request_scope(db: AsyncSession, tenant_id: UUID, user_id: UUID, role: Any) -> None:
    """Set the Postgres settings migration 0053's department_scope_policy
    reads. `role` is the caller's live access (TokenPayload) or an old
    persona string. Roles with all_departments get dept_scoped='0' (no
    restriction beyond the tenant); every other role gets the folder subtree they were
    granted -- which may be empty, meaning only their own root uploads.
    The policies fail closed: a dms_app session that never calls this (or
    set_tenant_wide_scope) sees no documents at all."""
    gucs = {"app.current_user_id": str(user_id), "app.dept_scoped": "0", "app.scope_folder_ids": ""}
    if not sees_all_departments(role):
        # Walking the folder tree needs to see it all; the policies fail
        # closed, so open it up just for this lookup, then narrow.
        await set_request_gucs(db, {"app.dept_scoped": "0"})
        folder_ids = await list_user_scope_folder_ids(db, tenant_id, user_id)
        gucs["app.dept_scoped"] = "1"
        gucs["app.scope_folder_ids"] = ",".join(str(f) for f in sorted(folder_ids, key=str))
    await set_request_gucs(db, gucs)


async def set_tenant_wide_scope(db: AsyncSession, tenant_id: UUID) -> None:
    """Tenant context plus explicit tenant-wide scope, for system actors
    that aren't a user request (e.g. the inbound-email connector)."""
    await set_request_gucs(db, {"app.current_tenant_id": str(tenant_id), "app.dept_scoped": "0",
                                "app.scope_folder_ids": "", "app.current_user_id": ""})


def request_scope_folder_ids(db: AsyncSession) -> Optional[Set[UUID]]:
    """The folder ids this request is confined to, or None when it isn't
    department-scoped. Sessions that never went through apply_request_scope
    (superuser sessions in the worker and tests, which bypass RLS) count as
    unscoped here; the database side fails closed regardless."""
    gucs = db.info.get(REQUEST_GUCS_KEY) or {}
    if gucs.get("app.dept_scoped") != "1":
        return None
    raw = gucs.get("app.scope_folder_ids") or ""
    return {UUID(f) for f in raw.split(",") if f}
