"""Custom roles (docs/features/custom-roles/, R6): tenant role management.

Guards, all enforced here rather than in the UI:
  - the locked Admin role (is_system) can't be edited, renamed or deleted
  - names are unique per tenant, case-insensitively (uq_iam_dg_roles_tenant_name)
  - only catalogue keys can be granted (app/permissions.py)
  - a role still held by users can't be deleted
  - no escalation (decision D2): a caller who isn't Admin can only create,
    edit or delete roles whose permissions -- before AND after -- are a
    subset of their own, and can never grant all_departments
"""
from typing import Iterable, List, Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.role import Role
from app.models.user import User
from app.permissions import PERMISSION_KEYS, PERMISSIONS, ROLE_TEMPLATES
from app.schemas.auth import TokenPayload
from app.services.audit_service import log_action

_MAX_NAME = 80


def catalogue() -> List[dict]:
    """The permission grid for the Roles screen, in display order."""
    groups: dict = {}
    for p in PERMISSIONS:
        groups.setdefault(p.group, []).append({"key": p.key, "label": p.label})
    return [{"group": g, "permissions": items} for g, items in groups.items()]


def templates() -> List[dict]:
    """"Start from template" (decision D1): the old personas as starting points."""
    return [{"key": k, "name": t.name, "all_departments": t.all_departments, "permissions": list(t.permissions)}
            for k, t in ROLE_TEMPLATES.items()]


def _serialize(role: Role, user_count: int = 0) -> dict:
    return {
        "id": str(role.id),
        "name": role.name,
        "is_system": role.is_system,
        "all_departments": role.all_departments,
        # The Admin role stores none: it holds every key implicitly.
        "permissions": sorted(PERMISSION_KEYS) if role.is_system else sorted(role.permissions or []),
        "user_count": user_count,
        "created_at": role.created_at.isoformat() if role.created_at else None,
        "updated_at": role.updated_at.isoformat() if role.updated_at else None,
    }


def _clean_name(name: str) -> str:
    name = (name or "").strip()
    if not name or len(name) > _MAX_NAME:
        raise HTTPException(status_code=422, detail=f"Role name must be 1-{_MAX_NAME} characters")
    return name


def _clean_permissions(perms: Iterable[str]) -> List[str]:
    perms = sorted(set(perms or []))
    unknown = [p for p in perms if p not in PERMISSION_KEYS]
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown permissions: {', '.join(unknown)}")
    return perms


def _guard_escalation(actor: TokenPayload, perms: Iterable[str], all_departments: bool) -> None:
    if actor.is_admin:
        return
    beyond = sorted(set(perms) - set(actor.permissions))
    if beyond:
        raise HTTPException(status_code=403, detail=f"You can't grant permissions you don't have: {', '.join(beyond)}")
    if all_departments:
        raise HTTPException(status_code=403, detail="Only an Admin can give a role access to all departments")


async def _user_count(db: AsyncSession, role_id: UUID) -> int:
    return (await db.execute(select(func.count(User.id)).where(User.role_id == role_id))).scalar() or 0


async def _get(db: AsyncSession, tenant_id: UUID, role_id: UUID) -> Role:
    role = await db.get(Role, role_id)
    if not role or role.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Role not found")
    return role


async def _flush_or_conflict(db: AsyncSession) -> None:
    try:
        await db.flush()
    except IntegrityError as exc:
        if "uq_iam_dg_roles_tenant_name" not in str(exc):
            raise
        await db.rollback()
        raise HTTPException(status_code=409, detail="A role with this name already exists")


async def list_roles(db: AsyncSession, tenant_id: UUID) -> List[dict]:
    counts = dict((await db.execute(
        select(User.role_id, func.count(User.id)).where(User.tenant_id == tenant_id, User.role_id.is_not(None))
        .group_by(User.role_id)
    )).all())
    roles = (await db.execute(
        select(Role).where(Role.tenant_id == tenant_id).order_by(Role.is_system.desc(), func.lower(Role.name))
    )).scalars().all()
    return [_serialize(r, counts.get(r.id, 0)) for r in roles]


async def create_role(db: AsyncSession, tenant_id: UUID, actor: TokenPayload, name: str,
                      permissions: Iterable[str], all_departments: bool = False) -> dict:
    name = _clean_name(name)
    perms = _clean_permissions(permissions)
    _guard_escalation(actor, perms, all_departments)
    role = Role(tenant_id=tenant_id, name=name, is_system=False, all_departments=bool(all_departments),
                permissions=perms, created_by_actor_id=UUID(actor.sub))
    db.add(role)
    await _flush_or_conflict(db)
    await log_action(db, UUID(actor.sub), tenant_id, "role.create", resource_type="role", resource_id=role.id,
                     details={"name": name, "permissions": perms, "all_departments": role.all_departments})
    return _serialize(role)


async def update_role(db: AsyncSession, tenant_id: UUID, actor: TokenPayload, role_id: UUID,
                      name: Optional[str] = None, permissions: Optional[Iterable[str]] = None,
                      all_departments: Optional[bool] = None) -> dict:
    role = await _get(db, tenant_id, role_id)
    if role.is_system:
        raise HTTPException(status_code=409, detail="The Admin role is locked and can't be changed")
    # Both the role as it is and as it will be must be within the caller's reach.
    _guard_escalation(actor, role.permissions or [], role.all_departments)

    changes: dict = {}
    if name is not None:
        new = _clean_name(name)
        if new != role.name:
            changes["name"] = {"from": role.name, "to": new}
            role.name = new
    if permissions is not None:
        new_perms = _clean_permissions(permissions)
        if new_perms != sorted(role.permissions or []):
            changes["permissions"] = {"from": sorted(role.permissions or []), "to": new_perms}
            role.permissions = new_perms
    if all_departments is not None and bool(all_departments) != role.all_departments:
        changes["all_departments"] = {"from": role.all_departments, "to": bool(all_departments)}
        role.all_departments = bool(all_departments)
    _guard_escalation(actor, role.permissions or [], role.all_departments)

    if changes:
        await _flush_or_conflict(db)
        await log_action(db, UUID(actor.sub), tenant_id, "role.update", resource_type="role", resource_id=role.id,
                         details=changes)
    return _serialize(role, await _user_count(db, role.id))


async def delete_role(db: AsyncSession, tenant_id: UUID, actor: TokenPayload, role_id: UUID) -> None:
    role = await _get(db, tenant_id, role_id)
    if role.is_system:
        raise HTTPException(status_code=409, detail="The Admin role is locked and can't be deleted")
    _guard_escalation(actor, role.permissions or [], role.all_departments)
    held_by = await _user_count(db, role.id)
    if held_by:
        raise HTTPException(status_code=409,
                            detail=f"{held_by} user(s) still have this role; move them to another role first")
    await log_action(db, UUID(actor.sub), tenant_id, "role.delete", resource_type="role", resource_id=role.id,
                     details={"name": role.name, "permissions": sorted(role.permissions or [])})
    await db.delete(role)
    await db.flush()
