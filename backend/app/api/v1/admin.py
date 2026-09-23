from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, case, extract, cast, Numeric
from datetime import datetime, timedelta
import uuid

from ...deps import get_tenant_db, require_role
from ...schemas.auth import TokenPayload
from ...models.user import User
from ...models.tenant import Tenant
from ...models.document import Document
from ...models.document_version import DocumentVersion
from ...models.chunk import Chunk
from ...models.folder import Folder
from ...models.chat_session import ChatSession
from ...models.audit_log import AuditLog
from ...models.api_log import ApiLog
from ...services import config_service

router = APIRouter()


class ConfigUpdateRequest(BaseModel):
    value: float


@router.get('/analytics')
async def get_admin_analytics(
    current_user: TokenPayload = Depends(require_role('it_admin')),
    db: AsyncSession = Depends(get_tenant_db),
):
    """Comprehensive DMS analytics for the Admin Panel, scoped to active tenant."""
    tenant_id = uuid.UUID(current_user.tenant_id)

    # ── System Overview ──
    total_users = (await db.execute(
        select(func.count(User.id)).where(User.tenant_id == tenant_id)
    )).scalar() or 0

    total_tenants = 1

    total_documents = (await db.execute(
        select(func.count(Document.id)).where(Document.tenant_id == tenant_id, Document.is_trashed == False)
    )).scalar() or 0

    total_trashed = (await db.execute(
        select(func.count(Document.id)).where(Document.tenant_id == tenant_id, Document.is_trashed == True)
    )).scalar() or 0

    total_folders = (await db.execute(
        select(func.count(Folder.id)).where(Folder.tenant_id == tenant_id, Folder.is_trashed == False)
    )).scalar() or 0

    total_storage = (await db.execute(
        select(func.coalesce(func.sum(DocumentVersion.file_size_bytes), 0))
        .join(Document, Document.current_version_id == DocumentVersion.id)
        .where(Document.tenant_id == tenant_id, Document.is_trashed == False)
    )).scalar() or 0

    total_chunks = (await db.execute(
        select(func.count(Chunk.id))
        .join(Document, Document.id == Chunk.document_id)
        .where(Chunk.tenant_id == tenant_id, Document.is_trashed == False)
    )).scalar() or 0

    total_chat_sessions = (await db.execute(
        select(func.count(ChatSession.id)).where(ChatSession.tenant_id == tenant_id)
    )).scalar() or 0

    total_audit_logs = (await db.execute(
        select(func.count(AuditLog.id)).where(AuditLog.actor_tenant_id == tenant_id)
    )).scalar() or 0

    # ── Documents by Status ──
    status_res = await db.execute(
        select(Document.status, func.count(Document.id))
        .where(Document.tenant_id == tenant_id, Document.is_trashed == False)
        .group_by(Document.status)
    )
    documents_by_status = {row[0]: row[1] for row in status_res.all()}

    # ── File Types Breakdown ──
    ft_res = await db.execute(
        select(
            Document.title,
            Document.mime_type,
            func.coalesce(DocumentVersion.file_size_bytes, 0)
        )
        .join(DocumentVersion, Document.current_version_id == DocumentVersion.id)
        .where(Document.tenant_id == tenant_id, Document.is_trashed == False)
    )

    type_counts: dict[str, dict] = {}
    for title, mime_type, size in ft_res.all():
        ext = "other"
        if title and "." in title:
            ext = title.rpartition(".")[2].lower()
        elif mime_type:
            ext = str(mime_type).lower()
        if ext not in type_counts:
            type_counts[ext] = {"count": 0, "size_bytes": 0}
        type_counts[ext]["count"] += 1
        type_counts[ext]["size_bytes"] += size

    file_types_breakdown = [
        {"extension": ext, "count": data["count"], "size_bytes": data["size_bytes"]}
        for ext, data in sorted(type_counts.items(), key=lambda x: x[1]["count"], reverse=True)
    ]

    # ── Upload Timeline (last 30 days) ──
    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
    timeline_res = await db.execute(
        select(
            func.date(Document.created_at).label("date"),
            func.count(Document.id).label("count")
        )
        .where(Document.tenant_id == tenant_id, Document.created_at >= thirty_days_ago)
        .group_by(func.date(Document.created_at))
        .order_by(func.date(Document.created_at))
    )
    upload_timeline = [
        {"date": str(row.date), "count": row.count}
        for row in timeline_res.all()
    ]

    # ── Top Uploaders (Top 10 by document count) ──
    uploaders_res = await db.execute(
        select(
            User.full_name,
            User.email,
            func.count(Document.id).label("doc_count"),
            func.coalesce(func.sum(DocumentVersion.file_size_bytes), 0).label("total_size")
        )
        # Joined on who created each document. Joining on tenant_id credited
        # every user with every document in the tenant (all 7 users showed
        # the same 45 uploads, found 2026-09-23).
        .join(Document, Document.created_by == User.id)
        .outerjoin(DocumentVersion, Document.current_version_id == DocumentVersion.id)
        .where(User.tenant_id == tenant_id, Document.tenant_id == tenant_id, Document.is_trashed == False)
        .group_by(User.id, User.full_name, User.email)
        .order_by(func.count(Document.id).desc())
        .limit(10)
    )
    top_uploaders = [
        {
            "full_name": row.full_name,
            "email": row.email,
            "doc_count": row.doc_count,
            "total_size": row.total_size,
        }
        for row in uploaders_res.all()
    ]

    # ── Storage Per Tenant ──
    # Reuses the overview totals above. The previous single query joined
    # documents AND users onto the tenant row, so every document was counted
    # once per user (448 documents / 1.7 GB shown for a real 45 / 231 MB).
    tenant_name = (await db.execute(select(Tenant.name).where(Tenant.id == tenant_id))).scalar()
    storage_per_tenant = [{
        "tenant_name": tenant_name,
        "doc_count": total_documents,
        "total_size": total_storage,
        "user_count": total_users,
    }] if tenant_name is not None else []

    # ── Recent Activity (Last 20 audit logs) ──
    activity_res = await db.execute(
        select(AuditLog.action, AuditLog.details, AuditLog.created_at)
        .where(AuditLog.actor_tenant_id == tenant_id)
        .order_by(AuditLog.created_at.desc())
        .limit(20)
    )
    recent_activity = [
        {
            "action": row.action,
            "resource_type": "system",
            "ip_address": "—",
            "timestamp": row.created_at.isoformat() if row.created_at else None,
        }
        for row in activity_res.all()
    ]

    return {
        "system_overview": {
            "total_users": total_users,
            "total_tenants": total_tenants,
            "total_documents": total_documents,
            "total_trashed": total_trashed,
            "total_folders": total_folders,
            "total_storage_bytes": total_storage,
            "total_chunks": total_chunks,
            "total_chat_sessions": total_chat_sessions,
            "total_audit_logs": total_audit_logs,
        },
        "documents_by_status": documents_by_status,
        "file_types_breakdown": file_types_breakdown,
        "upload_timeline": upload_timeline,
        "top_uploaders": top_uploaders,
        "storage_per_tenant": storage_per_tenant,
        "recent_activity": recent_activity,
    }


@router.get('/api-analytics')
async def get_api_analytics(
    current_user: TokenPayload = Depends(require_role('it_admin')),
    db: AsyncSession = Depends(get_tenant_db),
):
    """API call analytics from api_logs table, scoped to active tenant."""
    tenant_id = uuid.UUID(current_user.tenant_id)

    # ── Total API Calls ──
    total_calls = (await db.execute(
        select(func.count(ApiLog.id)).where(ApiLog.tenant_id == tenant_id)
    )).scalar() or 0

    # ── Calls Today ──
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    calls_today = (await db.execute(
        select(func.count(ApiLog.id)).where(ApiLog.tenant_id == tenant_id, ApiLog.created_at >= today_start)
    )).scalar() or 0

    # ── Average Response Time ──
    avg_response_time = (await db.execute(
        select(func.coalesce(func.avg(ApiLog.response_time_ms), 0)).where(ApiLog.tenant_id == tenant_id)
    )).scalar() or 0

    # ── Error Rate ──
    error_count = (await db.execute(
        select(func.count(ApiLog.id)).where(ApiLog.tenant_id == tenant_id, ApiLog.status_code >= 400)
    )).scalar() or 0
    error_rate = round((error_count / total_calls * 100), 2) if total_calls > 0 else 0

    # ── Calls by HTTP Method ──
    method_res = await db.execute(
        select(ApiLog.method, func.count(ApiLog.id))
        .where(ApiLog.tenant_id == tenant_id)
        .group_by(ApiLog.method)
        .order_by(func.count(ApiLog.id).desc())
    )
    calls_by_method = {row[0]: row[1] for row in method_res.all()}

    # ── Calls by Status Code Group ──
    status_res = await db.execute(
        select(
            case(
                (ApiLog.status_code < 300, "2xx"),
                (ApiLog.status_code < 400, "3xx"),
                (ApiLog.status_code < 500, "4xx"),
                else_="5xx"
            ).label("status_group"),
            func.count(ApiLog.id)
        )
        .where(ApiLog.tenant_id == tenant_id)
        .group_by("status_group")
    )
    calls_by_status = {row[0]: row[1] for row in status_res.all()}

    # ── Top Endpoints (Top 15) ──
    top_endpoints_res = await db.execute(
        select(
            ApiLog.method,
            ApiLog.path,
            func.count(ApiLog.id).label("call_count"),
            func.round(cast(func.avg(ApiLog.response_time_ms), Numeric), 2).label("avg_time_ms"),
        )
        .where(ApiLog.tenant_id == tenant_id)
        .group_by(ApiLog.method, ApiLog.path)
        .order_by(func.count(ApiLog.id).desc())
        .limit(15)
    )
    top_endpoints = [
        {
            "method": row.method,
            "path": row.path,
            "call_count": row.call_count,
            "avg_time_ms": float(row.avg_time_ms) if row.avg_time_ms else 0,
        }
        for row in top_endpoints_res.all()
    ]

    # ── API Timeline (last 24 hours, grouped by hour) ──
    twenty_four_hours_ago = datetime.utcnow() - timedelta(hours=24)
    timeline_res = await db.execute(
        select(
            extract('hour', ApiLog.created_at).label("hour"),
            func.count(ApiLog.id).label("count")
        )
        .where(ApiLog.tenant_id == tenant_id, ApiLog.created_at >= twenty_four_hours_ago)
        .group_by(extract('hour', ApiLog.created_at))
        .order_by(extract('hour', ApiLog.created_at))
    )
    api_timeline = [
        {"hour": int(row.hour), "count": row.count}
        for row in timeline_res.all()
    ]

    # ── Slowest Endpoints (Top 10 by avg response time) ──
    slowest_res = await db.execute(
        select(
            ApiLog.method,
            ApiLog.path,
            func.count(ApiLog.id).label("call_count"),
            func.round(cast(func.avg(ApiLog.response_time_ms), Numeric), 2).label("avg_time_ms"),
            func.round(cast(func.max(ApiLog.response_time_ms), Numeric), 2).label("max_time_ms"),
        )
        .where(ApiLog.tenant_id == tenant_id)
        .group_by(ApiLog.method, ApiLog.path)
        .having(func.count(ApiLog.id) >= 2)
        .order_by(func.avg(ApiLog.response_time_ms).desc())
        .limit(10)
    )
    slowest_endpoints = [
        {
            "method": row.method,
            "path": row.path,
            "call_count": row.call_count,
            "avg_time_ms": float(row.avg_time_ms) if row.avg_time_ms else 0,
            "max_time_ms": float(row.max_time_ms) if row.max_time_ms else 0,
        }
        for row in slowest_res.all()
    ]

    # ── Recent API Calls (Last 50) ──
    recent_res = await db.execute(
        select(
            ApiLog.method,
            ApiLog.path,
            ApiLog.status_code,
            ApiLog.response_time_ms,
            ApiLog.ip_address,
            ApiLog.created_at,
        )
        .where(ApiLog.tenant_id == tenant_id)
        .order_by(ApiLog.created_at.desc())
        .limit(50)
    )
    recent_calls = [
        {
            "method": row.method,
            "path": row.path,
            "status_code": row.status_code,
            "response_time_ms": round(row.response_time_ms, 2),
            "ip_address": row.ip_address or "—",
            "timestamp": row.created_at.isoformat() if row.created_at else None,
        }
        for row in recent_res.all()
    ]

    return {
        "overview": {
            "total_calls": total_calls,
            "calls_today": calls_today,
            "avg_response_time_ms": round(float(avg_response_time), 2),
            "error_rate": error_rate,
            "error_count": error_count,
        },
        "calls_by_method": calls_by_method,
        "calls_by_status": calls_by_status,
        "top_endpoints": top_endpoints,
        "slowest_endpoints": slowest_endpoints,
        "api_timeline": api_timeline,
        "recent_calls": recent_calls,
    }


@router.get('/config')
async def list_config_api(
    current_user: TokenPayload = Depends(require_role('it_admin')),
    db: AsyncSession = Depends(get_tenant_db),
):
    """T03 — sys_dg_config is global (no tenant_id column, see D2's own
    audit), so this genuinely lists every tenant's shared engineering
    thresholds. Read access still gated to it_admin, matching how the
    write side has to be — reading which thresholds are live is itself
    operational information, not something to expose to every role."""
    return await config_service.list_all_config(db)


@router.patch('/config/{key}')
async def update_config_api(
    key: str,
    body: ConfigUpdateRequest,
    current_user: TokenPayload = Depends(require_role('it_admin')),
    db: AsyncSession = Depends(get_tenant_db),
):
    tenant_id = uuid.UUID(current_user.tenant_id)
    actor_id = uuid.UUID(current_user.sub)
    return await config_service.set_config(db, key, body.value, actor_id, tenant_id)
