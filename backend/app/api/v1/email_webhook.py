"""FastAPI inbound webhook endpoint for Cloudflare Email Routing + Worker.

Receives raw emails forwarded by Cloudflare Worker, validates secret header,
extracts attachments, and ingests them into the DMS via connector_ingest_service.
"""
import base64
import hashlib
import hmac
import logging
import mimetypes
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.services import department_service
from app.services.connector_ingest_service import (
    already_ingested,
    get_connector_actor,
    ingest_bytes,
)
from app.services.email_utils import extract_attachments

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/connectors", tags=["Connectors"])


class EmailWebhookPayload(BaseModel):
    raw_email_b64: str = Field(
        ...,
        description="Base64-encoded raw RFC822 email message content",
    )


class IngestedAttachmentDetail(BaseModel):
    filename: str
    document_id: str
    file_hash: str


class EmailWebhookResponse(BaseModel):
    status: str
    attachments_processed: int
    attachments_ingested: int
    attachments_skipped_duplicate: int
    ingested_details: List[IngestedAttachmentDetail] = []
    errors: List[str] = []


@router.post("/email-inbound", response_model=EmailWebhookResponse)
async def receive_email_webhook(
    payload: EmailWebhookPayload,
    x_webhook_secret: Optional[str] = Header(None, alias="X-Webhook-Secret"),
    db: AsyncSession = Depends(get_db),
):
    """Receive raw email from Cloudflare Worker, extract attachments, and ingest them into DMS."""
    if not settings.email_webhook_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Email webhook endpoint is disabled",
        )

    # Secret header verification
    expected_secret = settings.email_webhook_secret
    if not x_webhook_secret or not hmac.compare_digest(x_webhook_secret, expected_secret):
        logger.warning("Email webhook authentication failed: invalid or missing X-Webhook-Secret")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing webhook secret header",
        )

    # Decode Base64 raw email
    try:
        raw_email_bytes = base64.b64decode(payload.raw_email_b64)
    except Exception as e:
        logger.error("Failed to base64 decode raw_email_b64 payload: %s", e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid base64 payload: {str(e)}",
        )

    attachments = extract_attachments(raw_email_bytes)
    if not attachments:
        logger.info("Email webhook received message with no file attachments")
        return EmailWebhookResponse(
            status="success",
            attachments_processed=0,
            attachments_ingested=0,
            attachments_skipped_duplicate=0,
            ingested_details=[],
            errors=[],
        )

    # D-2 fix — get_connector_actor looks up its fixed actor by email with
    # no tenant context (there isn't one yet); iam_dg_users' RLS policy
    # denies that by default, same as login/signup, unless this session's
    # cache already has the actor from an earlier, superuser-connected
    # caller. Never rely on that cache hit being lucky — set the same
    # narrow login-lookup exception explicitly first.
    from app.services.connector_ingest_service import DEFAULT_CONNECTOR_EMAIL
    await db.execute(
        text("SELECT set_config('app.login_lookup_email', :e, false)"),
        {"e": DEFAULT_CONNECTOR_EMAIL},
    )
    tenant_id, user_id = await get_connector_actor(db)
    # D-2 fix — this endpoint is authenticated by a shared webhook secret,
    # not a per-tenant JWT, so get_tenant_db (which needs one) doesn't
    # apply here; set the context explicitly the moment a tenant is known,
    # same pattern worker.py's ingestion pipeline already uses. Without
    # this, every INSERT below would be rejected by RLS's WITH CHECK once
    # this route's plain get_db() session is the restricted, non-superuser
    # AppSessionLocal connection. Session-scoped (false) so it survives
    # ingest_bytes()/upload_document()'s own mid-request db.commit() —
    # database.py's get_db() resets it centrally on the way out.
    # The connector is a system actor, not a department member, so it gets
    # explicit tenant-wide scope (migration 0053's policies fail closed).
    await department_service.set_tenant_wide_scope(db, tenant_id)
    ingested_count = 0
    skipped_count = 0
    ingested_details: List[IngestedAttachmentDetail] = []
    errors: List[str] = []

    for filename, content in attachments:
        file_hash = hashlib.sha256(content).hexdigest()

        if await already_ingested(db, tenant_id, file_hash):
            logger.info("Email webhook: '%s' already ingested (hash match), skipping", filename)
            skipped_count += 1
            continue

        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        try:
            resp = await ingest_bytes(content, filename, db, tenant_id, user_id, content_type=content_type)
            logger.info("Email webhook: successfully ingested '%s' as document %s", filename, resp.document_id)
            ingested_count += 1
            ingested_details.append(
                IngestedAttachmentDetail(
                    filename=filename,
                    document_id=str(resp.document_id),
                    file_hash=file_hash,
                )
            )
        except Exception as e:
            err_msg = f"Failed to ingest attachment '{filename}': {str(e)}"
            logger.error("Email webhook error: %s", err_msg)
            errors.append(err_msg)

    return EmailWebhookResponse(
        status="success" if not errors else "partial_success",
        attachments_processed=len(attachments),
        attachments_ingested=ingested_count,
        attachments_skipped_duplicate=skipped_count,
        ingested_details=ingested_details,
        errors=errors,
    )
