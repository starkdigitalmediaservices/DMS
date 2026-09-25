import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...deps import get_tenant_db, require_permission, require_tenant_access
from ...permissions import grants
from ...schemas.auth import TokenPayload
from ...services import role_service

router = APIRouter(prefix="/roles", tags=["Roles"])


async def _can_view_roles(current_user: TokenPayload = Depends(require_tenant_access)) -> TokenPayload:
    # The Users screen needs the role list to assign one, so either
    # permission may read it; only roles.manage may change it.
    if not (grants(current_user, "roles.manage") or grants(current_user, "users.manage")):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Your role does not allow this action (roles.manage)")
    return current_user


class RoleCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    permissions: List[str] = []
    all_departments: bool = False


class RoleUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=80)
    permissions: Optional[List[str]] = None
    all_departments: Optional[bool] = None


@router.get("")
async def list_roles_api(
    current_user: TokenPayload = Depends(_can_view_roles),
    db: AsyncSession = Depends(get_tenant_db),
):
    return await role_service.list_roles(db, uuid.UUID(current_user.tenant_id))


@router.get("/permissions")
async def list_permissions_api(current_user: TokenPayload = Depends(_can_view_roles)):
    return role_service.catalogue()


@router.get("/templates")
async def list_role_templates_api(current_user: TokenPayload = Depends(require_permission("roles.manage"))):
    return role_service.templates()


@router.post("", status_code=201)
async def create_role_api(
    body: RoleCreate,
    current_user: TokenPayload = Depends(require_permission("roles.manage")),
    db: AsyncSession = Depends(get_tenant_db),
):
    return await role_service.create_role(
        db, uuid.UUID(current_user.tenant_id), current_user, body.name, body.permissions, body.all_departments,
    )


@router.patch("/{role_id}")
async def update_role_api(
    role_id: uuid.UUID,
    body: RoleUpdate,
    current_user: TokenPayload = Depends(require_permission("roles.manage")),
    db: AsyncSession = Depends(get_tenant_db),
):
    return await role_service.update_role(
        db, uuid.UUID(current_user.tenant_id), current_user, role_id,
        name=body.name, permissions=body.permissions, all_departments=body.all_departments,
    )


@router.delete("/{role_id}", status_code=204)
async def delete_role_api(
    role_id: uuid.UUID,
    current_user: TokenPayload = Depends(require_permission("roles.manage")),
    db: AsyncSession = Depends(get_tenant_db),
):
    await role_service.delete_role(db, uuid.UUID(current_user.tenant_id), current_user, role_id)
