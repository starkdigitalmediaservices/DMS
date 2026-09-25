import uuid
from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ...schemas.auth import TokenPayload
from ...deps import get_tenant_db, require_tenant_access, require_permission
from ...services import records_service
from ...models.record_amendment import VALID_LEGAL_STATUSES

router = APIRouter(prefix="/records", tags=["Records"])


class RecordCreate(BaseModel):
    subject_node_id: uuid.UUID
    record_type: str
    base_fields: Dict[str, Any]
    base_evidence_fact_id: Optional[uuid.UUID] = None


@router.post("", status_code=201)
async def create_record_api(
    record_in: RecordCreate,
    current_user: TokenPayload = Depends(require_permission("records.create")),
    db: AsyncSession = Depends(get_tenant_db),
):
    tenant_id = uuid.UUID(current_user.tenant_id)
    user_id = uuid.UUID(current_user.sub)
    record = await records_service.create_record(
        db, tenant_id, record_in.subject_node_id, record_in.record_type, record_in.base_fields,
        base_evidence_fact_id=record_in.base_evidence_fact_id, created_by_actor_id=user_id,
    )
    return {
        "id": str(record.id),
        "subject_node_id": str(record.subject_node_id),
        "record_type": record.record_type,
        "base_fields": record.base_fields,
        "retention_class": record.retention_class,
    }


@router.get("/by-status/{legal_status}")
async def list_records_by_status_api(
    legal_status: str,
    current_user: TokenPayload = Depends(require_tenant_access),
    db: AsyncSession = Depends(get_tenant_db),
):
    if legal_status not in VALID_LEGAL_STATUSES:
        raise HTTPException(status_code=422, detail=f"legal_status must be one of {VALID_LEGAL_STATUSES}")
    tenant_id = uuid.UUID(current_user.tenant_id)
    return await records_service.list_records_by_legal_status(db, tenant_id, legal_status)


@router.get("/status-summary")
async def get_legal_status_summary_api(
    current_user: TokenPayload = Depends(require_tenant_access),
    db: AsyncSession = Depends(get_tenant_db),
):
    tenant_id = uuid.UUID(current_user.tenant_id)
    return await records_service.get_legal_status_summary(db, tenant_id)


@router.get("/{record_id}/history")
async def get_record_history_api(
    record_id: uuid.UUID,
    current_user: TokenPayload = Depends(require_tenant_access),
    db: AsyncSession = Depends(get_tenant_db),
):
    """T60/T62 — the base entry plus every amendment, in order: the
    'versions' view the entity 360 page's history panel reads from."""
    tenant_id = uuid.UUID(current_user.tenant_id)
    return await records_service.get_full_history(db, tenant_id, record_id)
