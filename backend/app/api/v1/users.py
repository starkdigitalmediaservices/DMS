import uuid
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...deps import get_tenant_db, require_permission
from ...schemas.auth import TokenPayload
from ...services import department_service, user_admin_service

router = APIRouter(prefix="/users", tags=["Users"])


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(..., min_length=2, max_length=50)
    role_id: Optional[uuid.UUID] = None
    role: Optional[str] = None  # legacy persona; pre-R11 Users screen only


class UserUpdate(BaseModel):
    role_id: Optional[uuid.UUID] = None
    role: Optional[str] = None  # legacy persona; pre-R11 Users screen only
    full_name: Optional[str] = Field(None, min_length=2, max_length=50)


@router.get("")
async def list_users_api(
    current_user: TokenPayload = Depends(require_permission("users.manage")),
    db: AsyncSession = Depends(get_tenant_db),
):
    return await user_admin_service.list_users(db, uuid.UUID(current_user.tenant_id))


@router.post("", status_code=201)
async def create_user_api(
    body: UserCreate,
    current_user: TokenPayload = Depends(require_permission("users.manage")),
    db: AsyncSession = Depends(get_tenant_db),
):
    """Add a user to this tenant. The response carries a one-time
    temporary password -- it isn't stored anywhere retrievable, so the
    admin has to pass it on; the user can change it from their profile."""
    return await user_admin_service.create_user(
        db, uuid.UUID(current_user.tenant_id), uuid.UUID(current_user.sub),
        body.email, body.full_name, role=body.role, role_id=body.role_id, actor=current_user,
    )


@router.patch("/{user_id}")
async def update_user_api(
    user_id: uuid.UUID,
    body: UserUpdate,
    current_user: TokenPayload = Depends(require_permission("users.manage")),
    db: AsyncSession = Depends(get_tenant_db),
):
    return await user_admin_service.update_user(
        db, uuid.UUID(current_user.tenant_id), uuid.UUID(current_user.sub), user_id,
        role=body.role, full_name=body.full_name, role_id=body.role_id, actor=current_user,
    )


class UserFolderGrant(BaseModel):
    folder_id: uuid.UUID


@router.post("/{user_id}/folders", status_code=201)
async def grant_user_folder_api(
    user_id: uuid.UUID,
    body: UserFolderGrant,
    current_user: TokenPayload = Depends(require_permission("departments.manage")),
    db: AsyncSession = Depends(get_tenant_db),
):
    """R14: share one folder (and everything under it) with one user,
    without a department. Folder access is managed by the same permission
    as department folder grants."""
    grant = await department_service.grant_user_folder(
        db, uuid.UUID(current_user.tenant_id), user_id, body.folder_id, uuid.UUID(current_user.sub))
    return {"id": str(grant.id), "user_id": str(user_id), "folder_id": str(body.folder_id)}


@router.delete("/{user_id}/folders/{folder_id}", status_code=204)
async def revoke_user_folder_api(
    user_id: uuid.UUID,
    folder_id: uuid.UUID,
    current_user: TokenPayload = Depends(require_permission("departments.manage")),
    db: AsyncSession = Depends(get_tenant_db),
):
    await department_service.revoke_user_folder(
        db, uuid.UUID(current_user.tenant_id), user_id, folder_id, uuid.UUID(current_user.sub))
