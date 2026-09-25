import uuid
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ...deps import get_tenant_db, require_permission
from ...schemas.auth import TokenPayload
from ...services import department_service

router = APIRouter(prefix="/departments", tags=["Departments"])


class DepartmentCreate(BaseModel):
    name: str


class DepartmentMemberAdd(BaseModel):
    user_id: uuid.UUID


class DepartmentFolderGrant(BaseModel):
    folder_id: uuid.UUID


@router.get("")
async def list_departments_api(
    current_user: TokenPayload = Depends(require_permission("departments.manage")),
    db: AsyncSession = Depends(get_tenant_db),
):
    return await department_service.list_departments(db, uuid.UUID(current_user.tenant_id))


@router.post("")
async def create_department_api(
    body: DepartmentCreate,
    current_user: TokenPayload = Depends(require_permission("departments.manage")),
    db: AsyncSession = Depends(get_tenant_db),
):
    tenant_id = uuid.UUID(current_user.tenant_id)
    actor_id = uuid.UUID(current_user.sub)
    dept = await department_service.create_department(db, tenant_id, body.name, actor_id)
    return {"id": str(dept.id), "name": dept.name}


@router.delete("/{department_id}", status_code=204)
async def delete_department_api(
    department_id: uuid.UUID,
    current_user: TokenPayload = Depends(require_permission("departments.manage")),
    db: AsyncSession = Depends(get_tenant_db),
):
    """Delete a department, its memberships and its folder grants. This
    revokes folder scope for any department-scoped member who had access
    only through this department."""
    tenant_id = uuid.UUID(current_user.tenant_id)
    actor_id = uuid.UUID(current_user.sub)
    await department_service.delete_department(db, tenant_id, department_id, actor_id)


@router.post("/{department_id}/members")
async def add_department_member_api(
    department_id: uuid.UUID,
    body: DepartmentMemberAdd,
    current_user: TokenPayload = Depends(require_permission("departments.manage")),
    db: AsyncSession = Depends(get_tenant_db),
):
    tenant_id = uuid.UUID(current_user.tenant_id)
    actor_id = uuid.UUID(current_user.sub)
    member = await department_service.add_department_member(db, tenant_id, department_id, body.user_id, actor_id)
    return {"id": str(member.id), "department_id": str(department_id), "user_id": str(body.user_id)}


@router.post("/{department_id}/folders")
async def grant_department_folder_api(
    department_id: uuid.UUID,
    body: DepartmentFolderGrant,
    current_user: TokenPayload = Depends(require_permission("departments.manage")),
    db: AsyncSession = Depends(get_tenant_db),
):
    tenant_id = uuid.UUID(current_user.tenant_id)
    actor_id = uuid.UUID(current_user.sub)
    grant = await department_service.grant_department_folder(db, tenant_id, department_id, body.folder_id, actor_id)
    return {"id": str(grant.id), "department_id": str(department_id), "folder_id": str(body.folder_id)}


@router.delete("/{department_id}/members/{user_id}", status_code=204)
async def remove_department_member_api(
    department_id: uuid.UUID,
    user_id: uuid.UUID,
    current_user: TokenPayload = Depends(require_permission("departments.manage")),
    db: AsyncSession = Depends(get_tenant_db),
):
    await department_service.remove_department_member(
        db, uuid.UUID(current_user.tenant_id), department_id, user_id, uuid.UUID(current_user.sub)
    )


@router.delete("/{department_id}/folders/{folder_id}", status_code=204)
async def revoke_department_folder_api(
    department_id: uuid.UUID,
    folder_id: uuid.UUID,
    current_user: TokenPayload = Depends(require_permission("departments.manage")),
    db: AsyncSession = Depends(get_tenant_db),
):
    await department_service.revoke_department_folder(
        db, uuid.UUID(current_user.tenant_id), department_id, folder_id, uuid.UUID(current_user.sub)
    )
