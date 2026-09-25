import uuid
from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ...schemas.auth import TokenPayload
from ...deps import get_tenant_db, require_permission
from ...services import export_service, report_service

router = APIRouter(prefix="/export", tags=["Export"])


@router.get("/entity/{node_id}")
async def export_entity_api(
    node_id: uuid.UUID,
    format: str = "json",
    mode: str = "general_export",
    current_user: TokenPayload = Depends(require_permission("export.report")),
    db: AsyncSession = Depends(get_tenant_db),
):
    tenant_id = uuid.UUID(current_user.tenant_id)
    user_id = uuid.UUID(current_user.sub)
    content, filename, content_type = await export_service.generate_export(
        db, tenant_id, user_id, node_id, format, mode,
    )
    return Response(
        content=content,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/entity/{node_id}/summary-report")
async def export_entity_summary_report_api(
    node_id: uuid.UUID,
    format: str = "pdf",
    mode: str = "general_export",
    current_user: TokenPayload = Depends(require_permission("export.report")),
    db: AsyncSession = Depends(get_tenant_db),
):
    """T77 — a narrative summary (not a raw data dump) with every
    unverified line explicitly tagged, deterministically, never left to
    a model's discretion."""
    tenant_id = uuid.UUID(current_user.tenant_id)
    user_id = uuid.UUID(current_user.sub)
    content, filename, content_type = await report_service.generate_summary_report(
        db, tenant_id, user_id, node_id, format, mode,
    )
    return Response(
        content=content,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
