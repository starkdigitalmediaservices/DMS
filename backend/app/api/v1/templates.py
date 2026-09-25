import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ...deps import get_tenant_db, require_permission, require_tenant_access
from ...schemas.auth import TokenPayload
from ...schemas.template import TemplateCreate, TemplateUpdate, TemplateResponse
from ...services import template_service

router = APIRouter(prefix="/templates", tags=["Templates"])


@router.get("", response_model=List[TemplateResponse])
async def list_templates_api(
    form_type: Optional[str] = None,
    current_user: TokenPayload = Depends(require_tenant_access),
    db: AsyncSession = Depends(get_tenant_db),
):
    return await template_service.list_templates(db, form_type=form_type)


@router.get("/{template_id}", response_model=TemplateResponse)
async def get_template_api(
    template_id: uuid.UUID,
    current_user: TokenPayload = Depends(require_tenant_access),
    db: AsyncSession = Depends(get_tenant_db),
):
    return await template_service.get_template_by_id(db, template_id)


@router.post("", response_model=TemplateResponse, status_code=201)
async def create_template_api(
    body: TemplateCreate,
    current_user: TokenPayload = Depends(require_permission("templates.manage")),
    db: AsyncSession = Depends(get_tenant_db),
):
    tenant_id = uuid.UUID(current_user.tenant_id)
    actor_id = uuid.UUID(current_user.sub)
    return await template_service.create_template(
        db, body.form_type, body.era_label, body.field_schema, body.layout, actor_id, tenant_id,
    )


@router.patch("/{template_id}", response_model=TemplateResponse)
async def update_template_api(
    template_id: uuid.UUID,
    body: TemplateUpdate,
    current_user: TokenPayload = Depends(require_permission("templates.manage")),
    db: AsyncSession = Depends(get_tenant_db),
):
    tenant_id = uuid.UUID(current_user.tenant_id)
    actor_id = uuid.UUID(current_user.sub)
    return await template_service.update_template(
        db, template_id, actor_id, tenant_id,
        form_type=body.form_type, era_label=body.era_label,
        field_schema=body.field_schema, layout=body.layout,
    )


@router.delete("/{template_id}", status_code=204)
async def delete_template_api(
    template_id: uuid.UUID,
    current_user: TokenPayload = Depends(require_permission("templates.manage")),
    db: AsyncSession = Depends(get_tenant_db),
):
    tenant_id = uuid.UUID(current_user.tenant_id)
    actor_id = uuid.UUID(current_user.sub)
    await template_service.delete_template(db, template_id, actor_id, tenant_id)
