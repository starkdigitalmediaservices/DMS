"""FastAPI Inbound Scanner Endpoint (Task T44 - TWAIN & Desktop Scanner API).

Provides:
1. `POST /api/v1/connectors/scan-inbound`: Accepts scanned file uploads
   (multipart/form-data or Base64 payload) directly from desktop scanner software,
   WebTWAIN bridges, or network scanner webhooks.
2. `GET /api/v1/connectors/scanner/status`: Returns status and configuration parameters
   for the scanner connector layer.
"""
import base64
import hashlib
import hmac
import logging
import mimetypes
import uuid
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.deps import get_current_user, bearer_scheme
from app.models.user import User
from app.schemas.auth import TokenPayload
from app.models.metadata_item import MetadataItem
from app.services.connector_ingest_service import (
    DEFAULT_CONNECTOR_EMAIL,
    already_ingested,
    get_connector_actor,
    get_or_create_folder_path,
    ingest_bytes,
)
from app.services.scanner_connector import assess_scan_quality, process_scanned_bytes

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/connectors", tags=["Connectors"])


class ScanWebhookPayload(BaseModel):
    raw_scan_b64: str = Field(..., description="Base64-encoded raw scanned image or PDF content")
    filename: str = Field("scanned_document.pdf", description="Filename for the scanned file")
    scanner_model: Optional[str] = Field("Generic WebTWAIN Scanner", description="Scanner hardware model")
    dpi: Optional[int] = Field(300, description="Scanning resolution in DPI")
    color_mode: Optional[str] = Field("color", description="Color mode: color, grayscale, bw")
    operator_notes: Optional[str] = Field(None, description="Optional operator scanning notes")


class IngestedScanDetail(BaseModel):
    filename: str
    document_id: str
    file_hash: str
    scanner_model: Optional[str] = None
    dpi: Optional[int] = None
    quality_flag: Optional[str] = None
    quality_report: Optional[Dict[str, Any]] = None


class ScanWebhookResponse(BaseModel):
    status: str
    scans_processed: int
    scans_ingested: int
    skipped_duplicate: int
    ingested_details: List[IngestedScanDetail] = []
    errors: List[str] = []


async def verify_scanner_auth(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Tuple[UUID, UUID]:
    """Verify authorization for scanner endpoint via Secret Header or JWT Bearer.

    Returns:
        (tenant_id_uuid, user_id_uuid)
    """
    x_webhook_secret = request.headers.get("X-Webhook-Secret")
    x_scanner_secret = request.headers.get("X-Scanner-Secret")
    secret_candidate = x_webhook_secret or x_scanner_secret
    expected_secret = settings.scanner_webhook_secret

    x_user_email = request.headers.get("X-User-Email")

    # 1. Check Secret Header Authentication
    if secret_candidate and hmac.compare_digest(secret_candidate, expected_secret):
        if x_user_email:
            # iam_dg_users' RLS only permits a by-email lookup when
            # app.login_lookup_email matches the row (the same narrow
            # exception login/signup rely on). Without setting it, this
            # SELECT matched nothing no matter who was asked for, and the
            # branch fell through to the connector actor — so a caller that
            # explicitly said "ingest as this user" silently got the scan
            # filed under a different account instead, with no error.
            # Naming a user is only reachable after the shared secret above
            # has already been verified, which is this endpoint's trust
            # boundary; that caller can ingest as the connector actor
            # regardless, so letting it name a user within the same
            # boundary grants nothing extra.
            requested_email = x_user_email.strip()
            await db.execute(
                text("SELECT set_config('app.login_lookup_email', :e, false)"),
                {"e": requested_email},
            )
            result = await db.execute(select(User).where(User.email == requested_email))
            user = result.scalar_one_or_none()
            if user:
                return user.tenant_id, user.id
            logger.warning(
                "Scanner auth: X-User-Email '%s' did not match any user — "
                "falling back to the default connector actor.",
                requested_email,
            )
        tenant_id, user_id = await get_connector_actor(db)
        return tenant_id, user_id

    # 2. Check JWT Bearer Token Authentication
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        try:
            credentials = await bearer_scheme(request)
            if credentials:
                user_payload = await get_current_user(credentials)
                if user_payload and user_payload.tenant_id:
                    return UUID(str(user_payload.tenant_id)), UUID(str(user_payload.sub))
        except Exception as e:
            logger.debug("Scanner auth JWT validation failed: %s", e)

    # Secret Warning Check
    if settings.scanner_enabled and settings.scanner_webhook_secret == "change_me_scanner_secret":
        logger.warning(
            "SECURITY WARNING: scanner_enabled is True but scanner_webhook_secret "
            "is using default value ('change_me_scanner_secret')."
        )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing authentication (X-Webhook-Secret header or Bearer JWT token required)",
    )


@router.post("/scan-inbound", response_model=ScanWebhookResponse)
async def receive_scan_inbound(
    request: Request,
    file: Optional[UploadFile] = File(None),
    scanner_model: Optional[str] = Form("Generic WebTWAIN Scanner"),
    dpi: Optional[int] = Form(300),
    color_mode: Optional[str] = Form("color"),
    operator_notes: Optional[str] = Form(None),
    json_payload: Optional[ScanWebhookPayload] = None,
    db: AsyncSession = Depends(get_db),
):
    """Receive scanned file from desktop scanner software, WebTWAIN agent, or network scanner webhook."""
    # 1. Early Content-Length check for 413 Payload Too Large
    max_bytes = settings.scanner_max_upload_size_mb * 1024 * 1024
    content_length = request.headers.get("Content-Length")
    if content_length:
        try:
            if int(content_length) > max_bytes:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"File size exceeds maximum allowed limit of {settings.scanner_max_upload_size_mb}MB",
                )
        except ValueError:
            pass

    # 2. Verify Authentication
    #
    # Same D-2 fix as email_webhook.py's receive_email_webhook: this route
    # is authenticated by a shared webhook secret or a bearer JWT, not a
    # per-tenant get_tenant_db() session, so RLS gets no tenant context for
    # free. Without the two set_config() calls below, verify_scanner_auth's
    # own get_connector_actor() lookup is blocked by iam_dg_users' RLS
    # (no tenant known yet -- same narrow login_lookup_email escape hatch
    # login/signup use), and every write after auth resolves (the "Scanned
    # Documents" folder, the ingested document, its quality-flag metadata)
    # is rejected by the tenant_isolation_policy's WITH CHECK -- which is
    # exactly what left this endpoint 500ing on every request.
    await db.execute(
        text("SELECT set_config('app.login_lookup_email', :e, false)"),
        {"e": DEFAULT_CONNECTOR_EMAIL},
    )
    tenant_id, user_id = await verify_scanner_auth(request, db=db)
    await db.execute(
        text("SELECT set_config('app.current_tenant_id', :t, false)"), {"t": str(tenant_id)}
    )

    # 3. Read File Content
    filename = "scanned_document.pdf"
    content_bytes = b""
    model_name = scanner_model
    dpi_val = dpi or settings.scanner_default_dpi

    if file:
        filename = file.filename or filename
        content_bytes = await file.read()
    else:
        # Check if JSON payload was sent
        try:
            body_bytes = await request.body()
            if body_bytes:
                payload_dict = ScanWebhookPayload.model_validate_json(body_bytes)
                content_bytes = base64.b64decode(payload_dict.raw_scan_b64)
                filename = payload_dict.filename
                model_name = payload_dict.scanner_model or model_name
                dpi_val = payload_dict.dpi or dpi_val
        except Exception as e:
            logger.error("Failed to parse scanner webhook payload: %s", e)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No file attached or invalid Base64 scan payload provided",
            )

    if not content_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Scanned file payload is empty",
        )

    # Enforce exact size check on byte length
    if len(content_bytes) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File size exceeds maximum allowed limit of {settings.scanner_max_upload_size_mb}MB",
        )

    # 4. Process Scanned Bytes (TIFF -> PDF conversion)
    guessed_mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    proc_content, proc_filename, proc_mime, _ = process_scanned_bytes(
        content_bytes, filename, mime_type=guessed_mime
    )

    # 4b. Assess Scan Quality
    quality_report = assess_scan_quality(proc_content)
    quality_flag = "needs_review" if not quality_report["passed"] else "clean"
    if not quality_report["passed"]:
        logger.warning("Scan inbound: quality warnings for '%s': %s", proc_filename, quality_report["warnings"])

    # 5. Check Hash Deduplication
    file_hash = hashlib.sha256(proc_content).hexdigest()

    if await already_ingested(db, tenant_id, file_hash):
        logger.info("Scan inbound: '%s' already ingested (hash match), skipping", proc_filename)
        return ScanWebhookResponse(
            status="success",
            scans_processed=1,
            scans_ingested=0,
            skipped_duplicate=1,
            ingested_details=[],
            errors=[],
        )

    # 6. Auto-map to "Scanned Documents" DMS folder
    try:
        folder_id = await get_or_create_folder_path(db, tenant_id, ["Scanned Documents"])
    except Exception as e:
        logger.warning("Scan inbound: failed to resolve 'Scanned Documents' folder: %s", e)
        folder_id = None

    # 7. Ingest into DMS via connector entry point
    try:
        resp = await ingest_bytes(
            proc_content,
            proc_filename,
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            content_type=proc_mime,
            folder_id=folder_id,
        )
        logger.info(
            "Scan inbound: successfully ingested '%s' (%s, %d DPI) as document %s",
            proc_filename,
            model_name,
            dpi_val,
            resp.document_id,
        )

        if not quality_report["passed"]:
            try:
                meta_flag = MetadataItem(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    document_id=resp.document_id,
                    key="quality_flag",
                    value={"flag": "needs_review", "warnings": quality_report["warnings"]},
                    source="scanner_connector",
                    confidence_score=0.9,
                )
                meta_report = MetadataItem(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    document_id=resp.document_id,
                    key="quality_report",
                    value=quality_report,
                    source="scanner_connector",
                    confidence_score=1.0,
                )
                db.add_all([meta_flag, meta_report])
                await db.commit()
            except Exception as meta_err:
                logger.warning("Scan inbound: failed to save quality metadata: %s", meta_err)

        return ScanWebhookResponse(
            status="success",
            scans_processed=1,
            scans_ingested=1,
            skipped_duplicate=0,
            ingested_details=[
                IngestedScanDetail(
                    filename=proc_filename,
                    document_id=str(resp.document_id),
                    file_hash=file_hash,
                    scanner_model=model_name,
                    dpi=dpi_val,
                    quality_flag=quality_flag,
                    quality_report=quality_report,
                )
            ],
            errors=[],
        )
    except Exception as e:
        err_msg = f"Failed to ingest scan '{proc_filename}': {str(e)}"
        logger.error("Scan inbound error: %s", err_msg)
        return ScanWebhookResponse(
            status="failure",
            scans_processed=1,
            scans_ingested=0,
            skipped_duplicate=0,
            ingested_details=[],
            errors=[err_msg],
        )


@router.get("/scanner/status")
async def get_scanner_status(
    current_user: TokenPayload = Depends(get_current_user),
):
    """Return status and configuration parameters for the scanner connector."""
    return {
        "enabled": settings.scanner_enabled,
        "inbox_dir": settings.scanner_inbox_dir,
        "default_dpi": settings.scanner_default_dpi,
        "max_upload_size_mb": settings.scanner_max_upload_size_mb,
        "poll_interval_seconds": settings.scanner_poll_interval_seconds,
        "target_folder": "Scanned Documents",
        "supported_formats": ["JPEG", "PNG", "TIFF (single and multi-page)", "PDF"],
        "tiff_conversion": "Multi-page TIFFs auto-converted to PDF/A-like archivable PDF with raw TIFF retained",
    }
