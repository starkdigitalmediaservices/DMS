import uuid
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...deps import get_tenant_db, require_role
from ...schemas.auth import TokenPayload
from ...services import user_admin_service

router = APIRouter(prefix="/users", tags=["Users"])


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(..., min_length=2, max_length=50)
    role: str


class UserUpdate(BaseModel):
    role: Optional[str] = None
    full_name: Optional[str] = Field(None, min_length=2, max_length=50)


@router.get("")
async def list_users_api(
    current_user: TokenPayload = Depends(require_role("it_admin")),
    db: AsyncSession = Depends(get_tenant_db),
):
    return await user_admin_service.list_users(db, uuid.UUID(current_user.tenant_id))


@router.post("", status_code=201)
async def create_user_api(
    body: UserCreate,
    current_user: TokenPayload = Depends(require_role("it_admin")),
    db: AsyncSession = Depends(get_tenant_db),
):
    """Add a user to this tenant. The response carries a one-time
    temporary password -- it isn't stored anywhere retrievable, so the
    admin has to pass it on; the user can change it from their profile."""
    return await user_admin_service.create_user(
        db, uuid.UUID(current_user.tenant_id), uuid.UUID(current_user.sub),
        body.email, body.full_name, body.role,
    )


@router.patch("/{user_id}")
async def update_user_api(
    user_id: uuid.UUID,
    body: UserUpdate,
    current_user: TokenPayload = Depends(require_role("it_admin")),
    db: AsyncSession = Depends(get_tenant_db),
):
    return await user_admin_service.update_user(
        db, uuid.UUID(current_user.tenant_id), uuid.UUID(current_user.sub), user_id,
        role=body.role, full_name=body.full_name,
    )
